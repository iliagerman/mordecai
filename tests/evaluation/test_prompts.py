"""Test cases for prompt validation and evaluation.

This module defines test cases used to evaluate agent prompt effectiveness.
Cases cover identity handling, memory operations, skill usage patterns, and
general agent behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests.evaluation.test_cases import Case

from tests.evaluation.test_cases import (
    MEMORY_TEST_CASES,
    SKILL_USAGE_TEST_CASES,
    GENERAL_BEHAVIOR_TEST_CASES,
    IDENTITY_TEST_CASES,
    Case,  # Import the actual Case class
)

# Re-export all test cases for easy importing
__all__ = [
    "PROMPT_TEST_CASES",
    "MEMORY_TEST_CASES",
    "SKILL_USAGE_TEST_CASES",
    "GENERAL_BEHAVIOR_TEST_CASES",
    "IDENTITY_TEST_CASES",
]

# Combined test cases for full prompt evaluation
PROMPT_TEST_CASES: list[Case] = [
    *IDENTITY_TEST_CASES,
    *MEMORY_TEST_CASES,
    *SKILL_USAGE_TEST_CASES,
    *GENERAL_BEHAVIOR_TEST_CASES,
]


def get_cases_by_category(category: str) -> list[Case]:
    """Get test cases by category name.

    Args:
        category: One of "identity", "memory", "skills", "general", or "all".

    Returns:
        List of test cases for the specified category.
    """
    category_map = {
        "identity": IDENTITY_TEST_CASES,
        "memory": MEMORY_TEST_CASES,
        "skills": SKILL_USAGE_TEST_CASES,
        "general": GENERAL_BEHAVIOR_TEST_CASES,
        "all": PROMPT_TEST_CASES,
    }
    return category_map.get(category.lower(), [])


def get_quick_eval_cases() -> list[Case]:
    """Get a quick subset of test cases for fast evaluation.

    Returns a smaller set of critical test cases suitable for
    pre-commit hooks or quick smoke tests.

    Returns:
        List of critical test cases.
    """
    return [
        # Identity - most important
        IDENTITY_TEST_CASES[0] if IDENTITY_TEST_CASES else None,
        # Memory - basic operation
        MEMORY_TEST_CASES[0] if MEMORY_TEST_CASES else None,
        # Skill usage - verify pattern
        SKILL_USAGE_TEST_CASES[0] if SKILL_USAGE_TEST_CASES else None,
        # General behavior
        GENERAL_BEHAVIOR_TEST_CASES[0] if GENERAL_BEHAVIOR_TEST_CASES else None,
    ]
