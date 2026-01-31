#!/usr/bin/env python
"""Manual test script for skill installation and agent usage.

This script tests the full flow:
1. Install skills from GitHub
2. Have the agent use them to accomplish tasks

Run with:
    python -m tests.manual.test_skill_usage

Requires:
- Valid AWS credentials for Bedrock OR OpenAI API key
- Network access to GitHub
"""

# This is a manual script and should not be collected/executed by pytest.
# It remains runnable via: python -m tests.manual.test_skill_usage
import sys

if "pytest" in sys.modules:
    import pytest

    pytest.skip("Manual script; skipped during automated test runs", allow_module_level=True)

import asyncio
import shutil
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.config import AgentConfig
from app.services.agent_service import AgentService
from app.services.skill_service import SkillInstallError, SkillService


# Skill URLs
PPTX_SKILL_URL = (
    "https://github.com/aws-samples/sample-strands-agents-agentskills/tree/main/skills/pptx"
)
YOUTUBE_SKILL_URL = (
    "https://github.com/michalparkola/tapestry-skills-for-claude-code/tree/main/youtube-transcript"
)

# Test video
TEST_VIDEO = "https://www.youtube.com/watch?v=kSno1-xOjwI"


async def test_pptx_skill(agent_service: AgentService):
    """Test the pptx skill by asking agent to create a presentation."""
    print("\n" + "=" * 60)
    print("Testing PPTX Skill")
    print("=" * 60)

    message = (
        "Using the pptx skill, create a simple 2-slide presentation "
        "about AI agents. Save it as test_presentation.pptx"
    )
    print(f"\nUser: {message}")

    try:
        response = await agent_service.process_message(user_id="test-user", message=message)
        print(f"\nAgent: {response}")
    except Exception as e:
        print(f"\nError: {e}")


async def test_youtube_skill(agent_service: AgentService):
    """Test the youtube-transcript skill."""
    print("\n" + "=" * 60)
    print("Testing YouTube Transcript Skill")
    print("=" * 60)

    message = (
        f"Using the youtube-transcript skill, get the transcript from this video: {TEST_VIDEO}"
    )
    print(f"\nUser: {message}")

    try:
        response = await agent_service.process_message(user_id="test-user", message=message)
        print(f"\nAgent: {response[:1000]}...")  # Truncate long response
    except Exception as e:
        print(f"\nError: {e}")


async def main():
    """Run manual skill tests."""
    print("=" * 60)
    print("Skill Installation and Usage Test")
    print("=" * 60)

    # Create temp directory for skills
    temp_dir = tempfile.mkdtemp(prefix="skill_test_")
    print(f"\nUsing temp directory: {temp_dir}")

    try:
        # Create config and services
        config = AgentConfig(
            telegram_bot_token="test-token",
            skills_base_dir=temp_dir,
            session_storage_dir=temp_dir,
        )
        skill_service = SkillService(config)
        agent_service = AgentService(config)

        # Install skills
        print("\n" + "-" * 40)
        print("Installing skills from GitHub...")
        print("-" * 40)

        try:
            print(f"\nInstalling pptx skill...")
            metadata = skill_service.install_skill(PPTX_SKILL_URL, "test-user")
            print(f"  ✓ Installed: {metadata.name}")

            # List files
            skill_dir = Path(temp_dir) / "pptx"
            files = list(skill_dir.rglob("*"))
            print(f"  Files: {len([f for f in files if f.is_file()])}")

        except SkillInstallError as e:
            print(f"  ✗ Failed: {e}")

        try:
            print(f"\nInstalling youtube-transcript skill...")
            metadata = skill_service.install_skill(YOUTUBE_SKILL_URL, "test-user")
            print(f"  ✓ Installed: {metadata.name}")

        except SkillInstallError as e:
            print(f"  ✗ Failed: {e}")

        # Show installed skills
        print("\n" + "-" * 40)
        print("Installed skills:")
        print("-" * 40)
        for skill in skill_service.list_skills("test-user"):
            print(f"  - {skill}")

        # Show system prompt
        print("\n" + "-" * 40)
        print("Agent system prompt (skills section):")
        print("-" * 40)
        prompt = agent_service._build_system_prompt("test-user")
        if "Installed Skills" in prompt:
            # Extract just the skills section
            start = prompt.find("## Installed Skills")
            print(prompt[start:])
        else:
            print("No skills section in prompt")

        # Test skills with agent
        print("\n" + "-" * 40)
        print("Testing skills with agent...")
        print("-" * 40)

        # Ask user which test to run
        print("\nWhich test would you like to run?")
        print("1. Test PPTX skill (create presentation)")
        print("2. Test YouTube skill (get transcript)")
        print("3. Both")
        print("4. Skip (just verify installation)")

        choice = input("\nEnter choice (1-4): ").strip()

        if choice in ("1", "3"):
            await test_pptx_skill(agent_service)

        if choice in ("2", "3"):
            await test_youtube_skill(agent_service)

        if choice == "4":
            print("\nSkipping agent tests.")

    finally:
        # Cleanup
        print("\n" + "-" * 40)
        print("Cleaning up...")
        shutil.rmtree(temp_dir, ignore_errors=True)
        print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
