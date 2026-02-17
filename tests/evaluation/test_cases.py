"""Test case definitions for agent evaluation.

This module contains the actual test case definitions organized by category.
Each case tests a specific aspect of agent behavior or prompt effectiveness.
"""

from __future__ import annotations

try:
    from strands_evals import Case
except ImportError:
    # Create a fallback Case class for when strands_evals is not installed
    from dataclasses import dataclass

    @dataclass
    class Case:
        """Fallback Case class when strands_evals is not available."""

        input: str
        expected_output: str

# =============================================================================
# Identity Test Cases
# =============================================================================

IDENTITY_TEST_CASES: list[Case] = [
    Case(
        input="What's your name?",
        expected_output=(
            "Agent should identify with configured name from memory, "
            "or say it doesn't have a name yet and ask the user for one. "
            "Should NEVER say it is Claude, ChatGPT, or any generic AI."
        ),
    ),
    Case(
        input="Who are you?",
        expected_output=(
            "Agent should describe itself as an AI assistant with access to tools. "
            "Should use its configured name if available. "
            "Should not claim to be Claude, ChatGPT, etc."
        ),
    ),
    Case(
        input="Call you Jarvis",
        expected_output=(
            "Agent should use the set_agent_name tool to store the name 'Jarvis' in memory. "
            "Should confirm the name has been stored. "
            "If the tool fails, should NOT claim to remember the name."
        ),
    ),
]

# =============================================================================
# Memory Test Cases
# =============================================================================

MEMORY_TEST_CASES: list[Case] = [
    Case(
        input="Remember that I prefer coffee over tea",
        expected_output=(
            "Agent should use the remember_preference tool to store this preference. "
            "Should confirm the preference has been stored in long-term memory."
        ),
    ),
    Case(
        input="My favorite color is blue",
        expected_output=(
            "Agent should use the remember_fact tool to store this fact. "
            "Should confirm the fact has been stored in long-term memory."
        ),
    ),
    Case(
        input="What are my preferences?",
        expected_output=(
            "Agent should use the search_memory tool to look up stored preferences. "
            "Should report what preferences have been previously stored."
        ),
    ),
    Case(
        input="What do you know about me?",
        expected_output=(
            "Agent should use the search_memory tool to retrieve stored facts and preferences. "
            "Should summarize what it has learned about the user."
        ),
    ),
    Case(
        input="Forget my color preference",
        expected_output=(
            "Agent should use forget_memory with dry_run=True first to show what will be deleted, "
            "then ask for confirmation before calling with dry_run=False. "
            "Should be honest about what was actually deleted."
        ),
    ),
]

# =============================================================================
# Skill Usage Test Cases
# =============================================================================

SKILL_USAGE_TEST_CASES: list[Case] = [
    Case(
        input="List available skills",
        expected_output=(
            "Agent should first read SKILL.md files from the skills directory "
            "before listing skills. Should use file_read to examine SKILL.md files, "
            "then report the skills found with their descriptions."
        ),
    ),
    Case(
        input="What skills do I have installed?",
        expected_output=(
            "Agent should read SKILL.md files from the skills directory "
            "to get accurate skill descriptions. Should not guess or make up skill names."
        ),
    ),
    Case(
        input="Search the web for latest Python version",
        expected_output=(
            "Agent should first file_read the tavily-search SKILL.md to understand the command pattern, "
            "then use shell() to run the correct node command with the search query. "
            "Should NOT guess the command or assume it knows the pattern."
        ),
    ),
    Case(
        input="Install the pandas Python package",
        expected_output=(
            "Agent should first file_read the dependency-installer SKILL.md, "
            "then run the correct pip install command via shell(). "
            "Should verify the package was installed successfully."
        ),
    ),
]

# =============================================================================
# General Behavior Test Cases
# =============================================================================

GENERAL_BEHAVIOR_TEST_CASES: list[Case] = [
    Case(
        input="Create a file called hello.txt with text 'Hello World'",
        expected_output=(
            "Agent should use file_write to create the file at the full working folder path. "
            "Should use the correct working folder path in the file path. "
            "Should confirm the file was created."
        ),
    ),
    Case(
        input="Read the file hello.txt",
        expected_output=(
            "Agent should use file_read to read the file from the working folder. "
            "Should use the full path including the working folder. "
            "Should display the file contents."
        ),
    ),
    Case(
        input="List my scheduled tasks",
        expected_output=(
            "Agent should use list_cron_tasks tool to show scheduled tasks. "
            "Should not use system crontab or shell commands for scheduling."
        ),
    ),
    Case(
        input="Schedule a daily reminder at 9am",
        expected_output=(
            "Agent should use create_cron_task tool with proper cron expression '0 9 * * *'. "
            "The instructions should describe what the agent should do when the task fires. "
            "Should confirm the task was created."
        ),
    ),
    Case(
        input="What's in my working folder?",
        expected_output=(
            "Agent should use shell with 'ls' command and the correct work_dir parameter. "
            "Should set work_dir to the user's working folder path. "
            "Should list the folder contents."
        ),
    ),
    Case(
        input="I need help with a coding task",
        expected_output=(
            "Agent should respond helpfully and ask for more details about the task. "
            "Should offer specific assistance options. "
            "Should not ask 'How can I help?' - should be more specific."
        ),
    ),
]


# =============================================================================
# Quick Test Cases (for pre-commit / fast evaluation)
# =============================================================================

QUICK_EVAL_CASES: list[Case] = [
    IDENTITY_TEST_CASES[0],  # "What's your name?"
    MEMORY_TEST_CASES[0],  # "Remember that I prefer coffee..."
    SKILL_USAGE_TEST_CASES[0],  # "List available skills"
    GENERAL_BEHAVIOR_TEST_CASES[0],  # "Create a file..."
]


def get_all_cases() -> list[Case]:
    """Get all test cases."""
    return [
        *IDENTITY_TEST_CASES,
        *MEMORY_TEST_CASES,
        *SKILL_USAGE_TEST_CASES,
        *GENERAL_BEHAVIOR_TEST_CASES,
    ]


def get_cases_by_tags(*tags: str) -> list[Case]:
    """Get test cases filtered by tags.

    Args:
        *tags: Tags to filter by. Options: "identity", "memory", "skills", "general".

    Returns:
        Filtered list of test cases.
    """
    tag_map = {
        "identity": IDENTITY_TEST_CASES,
        "memory": MEMORY_TEST_CASES,
        "skills": SKILL_USAGE_TEST_CASES,
        "general": GENERAL_BEHAVIOR_TEST_CASES,
    }

    cases: list[Case] = []
    for tag in tags:
        cases.extend(tag_map.get(tag, []))
    return cases
