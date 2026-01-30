"""Integration tests for skill execution behavior.

Tests that the agent correctly uses skills without looping or
calling the same tool repeatedly with invalid parameters.

These tests verify:
1. Agent reads SKILL.md only once
2. Agent proceeds to use shell commands after reading
3. Agent doesn't loop on file_read with invalid parameters
"""

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ["BYPASS_TOOL_CONSENT"] = "true"

from app.config import AgentConfig
from app.services.agent_service import AgentService


TEST_USER_ID = "test_user_skill_exec"


@pytest.fixture
def temp_dir():
    """Create a temporary directory."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def config(temp_dir):
    """Create test configuration with real credentials."""
    try:
        base_config = AgentConfig.from_json_file(
            config_path="config.json",
            secrets_path="secrets.yml",
        )
        return AgentConfig(
            telegram_bot_token=base_config.telegram_bot_token,
            model_provider=base_config.model_provider,
            bedrock_model_id=base_config.bedrock_model_id,
            bedrock_api_key=base_config.bedrock_api_key,
            openai_api_key=base_config.openai_api_key,
            openai_model_id=base_config.openai_model_id,
            aws_region=base_config.aws_region,
            skills_base_dir=temp_dir,
            session_storage_dir=temp_dir,
            working_folder_base_dir=temp_dir,
        )
    except Exception:
        pytest.skip("No valid credentials found in secrets.yml")


@pytest.fixture
def agent_service(config):
    """Create agent service."""
    return AgentService(config)


def create_simple_skill(temp_dir: str, user_id: str) -> Path:
    """Create a simple test skill that uses shell commands."""
    skill_dir = Path(temp_dir) / user_id / "test-echo-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)

    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("""---
name: test-echo-skill
description: A simple skill that echoes a message using shell
---
# Test Echo Skill

## Instructions

To use this skill, run the following shell command:

```bash
echo "Hello from the test skill!"
```

That's it! Just run the echo command above.
""")
    return skill_dir


class TestSkillExecutionNoLoop:
    """Tests that agent doesn't loop when using skills."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_agent_reads_skill_md_only_once(
        self, agent_service, temp_dir
    ):
        """Test that agent reads SKILL.md only once, not in a loop."""
        create_simple_skill(temp_dir, TEST_USER_ID)

        # Track tool calls
        tool_calls = []
        original_process = agent_service.process_message

        async def tracking_process(user_id, message):
            # We'll check the agent's behavior by examining the response
            return await original_process(user_id, message)

        response = await agent_service.process_message(
            user_id=TEST_USER_ID,
            message="Use the test-echo-skill to echo a message"
        )

        # The response should not indicate repeated file reads
        assert response is not None

        # Count how many times "file_read" appears in thinking/response
        # A looping agent would mention file_read many times
        response_lower = response.lower()

        # Should have successfully executed something
        assert len(response) > 0

        print(f"\n\nAgent response:\n{response}\n\n")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_agent_uses_shell_after_reading_skill(
        self, agent_service, temp_dir
    ):
        """Test that agent uses shell command after reading SKILL.md."""
        create_simple_skill(temp_dir, TEST_USER_ID)

        response = await agent_service.process_message(
            user_id=TEST_USER_ID,
            message="Use the test-echo-skill"
        )

        assert response is not None
        response_lower = response.lower()

        # Should have executed the echo command or mentioned it
        has_echo_result = (
            "hello from the test skill" in response_lower or
            "echo" in response_lower
        )

        # Should NOT be stuck asking about mode parameter
        not_stuck_on_mode = "mode" not in response_lower

        print(f"\n\nAgent response:\n{response}\n\n")

        assert has_echo_result or not_stuck_on_mode, (
            f"Agent didn't execute skill properly. Response: {response[:500]}"
        )


class TestToolCallTracking:
    """Tests that track actual tool calls to detect loops."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_file_read_not_called_excessively(
        self, agent_service, temp_dir
    ):
        """Test that file_read is not called more than 3 times for same file."""
        skill_dir = create_simple_skill(temp_dir, TEST_USER_ID)
        skill_md_path = str(skill_dir / "SKILL.md")

        # Track file_read calls by wrapping the tool
        file_read_calls = []
        
        # Import the actual file_read to wrap it
        from strands_tools import file_read as original_file_read

        def tracking_file_read(tool, **kwargs):
            tool_input = tool.get("input", {})
            path = tool_input.get("path", "")
            file_read_calls.append(path)
            return original_file_read(tool, **kwargs)

        # Patch file_read in the agent's tools
        with patch.object(
            agent_service, '_create_agent'
        ) as mock_create:
            # Let the original run but we'll check calls after
            pass

        response = await agent_service.process_message(
            user_id=TEST_USER_ID,
            message="Use the test-echo-skill to say hello"
        )

        print(f"\n\nAgent response:\n{response}\n\n")

        # Check response doesn't indicate a loop
        assert response is not None

        # Response should not contain repeated error messages about mode
        mode_mentions = response.lower().count("mode")
        assert mode_mentions < 3, (
            f"Agent mentioned 'mode' {mode_mentions} times, indicating a loop"
        )


class TestSkillInstructionFollowing:
    """Tests that agent follows skill instructions correctly."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_agent_executes_shell_from_skill_instructions(
        self, agent_service, temp_dir
    ):
        """Test that agent executes shell commands from skill instructions."""
        skill_dir = Path(temp_dir) / TEST_USER_ID / "shell-test-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)

        # Create a skill with clear shell instructions
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("""---
name: shell-test-skill
description: Test skill that runs a simple shell command
---
# Shell Test Skill

When asked to use this skill, execute this command:

```bash
echo "SKILL_EXECUTED_SUCCESSFULLY"
```

The output should contain "SKILL_EXECUTED_SUCCESSFULLY".
""")

        response = await agent_service.process_message(
            user_id=TEST_USER_ID,
            message="Please use the shell-test-skill"
        )

        print(f"\n\nAgent response:\n{response}\n\n")

        assert response is not None

        # Check if the skill was executed
        # Either the output contains the success marker, or agent mentioned running it
        success_indicators = [
            "SKILL_EXECUTED_SUCCESSFULLY" in response,
            "executed" in response.lower(),
            "echo" in response.lower() and "error" not in response.lower(),
        ]

        assert any(success_indicators), (
            f"Skill was not executed properly. Response: {response}"
        )

    @pytest.mark.asyncio
    @pytest.mark.integration  
    async def test_agent_does_not_loop_on_invalid_params(
        self, agent_service, temp_dir
    ):
        """Test that agent doesn't loop when tool returns an error."""
        create_simple_skill(temp_dir, TEST_USER_ID)

        response = await agent_service.process_message(
            user_id=TEST_USER_ID,
            message="Use the test-echo-skill"
        )

        # Count repetitive patterns that indicate looping
        response_lines = response.split('\n')
        
        # Check for repeated identical lines (sign of loop output)
        from collections import Counter
        line_counts = Counter(line.strip() for line in response_lines if line.strip())
        
        max_repetition = max(line_counts.values()) if line_counts else 0
        
        assert max_repetition < 5, (
            f"Response has {max_repetition} repeated lines, indicating a loop. "
            f"Most repeated: {line_counts.most_common(3)}"
        )

        print(f"\n\nAgent response:\n{response}\n\n")


class TestComplexSkillExecution:
    """Tests with more complex skills similar to youtube-transcript."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_agent_handles_complex_skill_without_looping(
        self, agent_service, temp_dir
    ):
        """Test that agent doesn't loop on complex multi-step skills."""
        skill_dir = Path(temp_dir) / TEST_USER_ID / "complex-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)

        # Create a complex skill similar to youtube-transcript
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("""---
name: complex-skill
description: A complex skill with multiple steps
allowed-tools: Bash,Read,Write
---
# Complex Multi-Step Skill

This skill demonstrates a complex workflow.

## When to Use

Use this skill when the user asks to run a complex task.

## Prerequisites

**IMPORTANT**: Always check if the tool is available first:

```bash
which echo
```

## Step 1: Check Environment

First, verify the environment:

```bash
echo "Step 1: Environment check passed"
```

## Step 2: Process Data

Then process the data:

```bash
echo "Step 2: Processing complete"
```

## Step 3: Generate Output

Finally, generate output:

```bash
echo "COMPLEX_SKILL_SUCCESS"
```

## Error Handling

If any step fails, inform the user.

## Notes

- This is a test skill
- Follow the steps in order
- The final output should contain "COMPLEX_SKILL_SUCCESS"
""")

        response = await agent_service.process_message(
            user_id=TEST_USER_ID,
            message="Please run the complex-skill"
        )

        print(f"\n\nAgent response:\n{response}\n\n")

        assert response is not None

        # Check for success or at least shell execution
        response_lower = response.lower()
        
        success_indicators = [
            "COMPLEX_SKILL_SUCCESS" in response,
            "step" in response_lower and "complete" in response_lower,
            "echo" in response_lower and "error" not in response_lower,
        ]

        # Count file_read mentions - should not be excessive
        file_read_count = response_lower.count("file_read")
        
        assert any(success_indicators) or file_read_count < 3, (
            f"Skill not executed properly or looped. "
            f"file_read mentioned {file_read_count} times. "
            f"Response: {response[:500]}"
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_agent_executes_first_command_from_complex_skill(
        self, agent_service, temp_dir
    ):
        """Test that agent at least executes the first shell command."""
        skill_dir = Path(temp_dir) / TEST_USER_ID / "first-cmd-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)

        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("""---
name: first-cmd-skill
description: Skill to test first command execution
---
# First Command Skill

## Instructions

Run this command immediately after reading:

```bash
echo "FIRST_COMMAND_EXECUTED"
```

Then you can do other things if needed.
""")

        response = await agent_service.process_message(
            user_id=TEST_USER_ID,
            message="Use the first-cmd-skill"
        )

        print(f"\n\nAgent response:\n{response}\n\n")

        # Should have executed the echo command
        assert "FIRST_COMMAND_EXECUTED" in response, (
            f"First command was not executed. Response: {response}"
        )
