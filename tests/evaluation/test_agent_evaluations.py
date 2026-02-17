"""Agent evaluation tests using Strands Evals + AgentCore.

These tests evaluate agent behavior, prompt effectiveness, and skill usage
patterns using Amazon Bedrock AgentCore's evaluation API.

Tests require the bedrock-agentcore evaluation module:
    pip install 'bedrock-agentcore[strands-agents-evals]'

Run with:
    pytest tests/evaluation/test_agent_evaluations.py -m evaluation -v

Run quick evaluation only:
    pytest tests/evaluation/test_agent_evaluations.py::test_prompt_helpfulness_quick -v
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

# Import test cases - these use a fallback Case class if strands_evals is not available
from tests.evaluation.test_cases import (
    GENERAL_BEHAVIOR_TEST_CASES,
    IDENTITY_TEST_CASES,
    MEMORY_TEST_CASES,
    QUICK_EVAL_CASES,
    SKILL_USAGE_TEST_CASES,
)

# Import framework classes
from tests.evaluation.eval_framework import AgentEvaluator, EvaluationMetrics

logger = logging.getLogger(__name__)


# =============================================================================
# Pytest Configuration
# =============================================================================

def pytest_configure(config):
    """Configure pytest markers."""
    config.addinivalue_line(
        "markers",
        "evaluation: marks tests as evaluation tests using AgentCore API (may be slow and require AWS)",
    )
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow (may take more than a few seconds)",
    )


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_agent():
    """Create a mock agent for testing.

    Returns a mock that can be called with input and returns a response.
    """
    agent = MagicMock()

    # Configure default responses
    def mock_run(input_text: str) -> str:
        responses = {
            "What's your name?": "I don't have a name yet. What would you like to call me?",
            "Who are you?": "I'm an AI assistant with access to various tools.",
            "List available skills": "I have tavily-search, excel, and other skills available.",
        }
        return responses.get(input_text, f"Response to: {input_text}")

    agent.side_effect = mock_run
    agent.return_value = MagicMock(__str__=lambda self: "Mock agent response")

    return agent


@pytest.fixture
def evaluator(config):
    """Create an AgentEvaluator instance."""
    return AgentEvaluator(config=config)


@pytest.fixture
def evaluator_with_region():
    """Create an AgentEvaluator with explicit region."""
    return AgentEvaluator(region="us-west-2")


# =============================================================================
# Helper Functions
# =============================================================================

def skip_if_no_evaluation_module() -> None:
    """Skip test if bedrock_agentcore evaluation module is not available."""
    try:
        import bedrock_agentcore.evaluation  # noqa: F401
    except ImportError:
        pytest.skip(
            "bedrock_agentcore evaluation module not available. "
            "Install with: pip install 'bedrock-agentcore[strands-agents-evals]'"
        )


def skip_if_no_strands_evals() -> None:
    """Skip test if strands_evals is not available."""
    try:
        import strands_evals  # noqa: F401
    except ImportError:
        pytest.skip(
            "strands_evals module not available. "
            "Install with: pip install strands-evals"
        )


# =============================================================================
# Framework Tests
# =============================================================================

class TestAgentEvaluator:
    """Test the AgentEvaluator framework itself."""

    def test_evaluator_initialization(self, evaluator_with_region):
        """Test that evaluator initializes correctly."""
        assert evaluator_with_region._region == "us-west-2"
        assert evaluator_with_region._telemetry is None

    def test_evaluator_initialization_with_config(self, config):
        """Test evaluator initialization with config."""
        evaluator = AgentEvaluator(config=config)
        # Should use region from config or default
        assert evaluator._region == "us-west-2"

    def test_telemetry_lazy_initialization(self, evaluator):
        """Test that telemetry is initialized lazily."""
        skip_if_no_strands_evals()

        # Telemetry should be None initially
        assert evaluator._telemetry is None

        # Accessing the property should create it
        telemetry = evaluator.telemetry
        assert telemetry is not None
        assert evaluator._telemetry is not None

    def test_telemetry_reset(self, evaluator):
        """Test that telemetry can be reset."""
        skip_if_no_strands_evals()

        # Initialize telemetry
        _ = evaluator.telemetry
        assert evaluator._telemetry is not None

        # Reset should clear it
        evaluator.reset_telemetry()
        assert evaluator._telemetry is None

    def test_create_evaluator(self, evaluator):
        """Test creating an evaluator instance."""
        skip_if_no_evaluation_module()

        eval_instance = evaluator.create_evaluator("Builtin.Helpfulness")
        assert eval_instance is not None

    def test_create_evaluator_with_custom_score(self, evaluator):
        """Test creating evaluator with custom pass score."""
        skip_if_no_evaluation_module()

        eval_instance = evaluator.create_evaluator(
            "Builtin.Helpfulness",
            test_pass_score=0.8
        )
        assert eval_instance is not None


class TestEvaluationMetrics:
    """Test the EvaluationMetrics helper class."""

    def test_get_pass_rate_all_passed(self):
        """Test pass rate calculation when all tests pass."""
        report = MagicMock()
        report.test_passes = [True, True, True]
        assert EvaluationMetrics.get_pass_rate(report) == 1.0

    def test_get_pass_rate_partial(self):
        """Test pass rate calculation with mixed results."""
        report = MagicMock()
        report.test_passes = [True, False, True, False]
        assert EvaluationMetrics.get_pass_rate(report) == 0.5

    def test_get_pass_rate_empty(self):
        """Test pass rate calculation with no tests."""
        report = MagicMock()
        report.test_passes = []
        assert EvaluationMetrics.get_pass_rate(report) == 0.0

    def test_format_report(self):
        """Test report formatting."""
        report = MagicMock()
        report.evaluator_id = "Builtin.Helpfulness"
        report.overall_score = 0.85
        report.test_passes = [True, True, False]

        formatted = EvaluationMetrics.format_report(report)
        assert "Builtin.Helpfulness" in formatted
        assert "0.85" in formatted
        assert "66.7%" in formatted or "67%" in formatted

    def test_check_threshold_pass(self):
        """Test threshold check when both metrics pass."""
        report = MagicMock()
        report.overall_score = 0.8
        report.test_passes = [True, True, True]

        passed, msg = EvaluationMetrics.check_threshold(report, min_score=0.7, min_pass_rate=0.7)
        assert passed is True
        assert msg == "Passed"

    def test_check_threshold_fail_score(self):
        """Test threshold check when score fails."""
        report = MagicMock()
        report.overall_score = 0.5
        report.test_passes = [True, True, True]

        passed, msg = EvaluationMetrics.check_threshold(report, min_score=0.7, min_pass_rate=0.7)
        assert passed is False
        assert "score" in msg.lower()

    def test_check_threshold_fail_pass_rate(self):
        """Test threshold check when pass rate fails."""
        report = MagicMock()
        report.overall_score = 0.8
        report.test_passes = [True, False, False]

        passed, msg = EvaluationMetrics.check_threshold(report, min_score=0.7, min_pass_rate=0.7)
        assert passed is False
        assert "pass rate" in msg.lower()


# =============================================================================
# Integration Tests (require AWS credentials)
# =============================================================================

@pytest.mark.evaluation
@pytest.mark.slow
class TestAgentEvaluations:
    """Integration tests that actually evaluate agents.

    These tests require valid AWS credentials and will make real
    calls to the AgentCore Evaluation API.
    """

    def test_evaluate_single_case(self, evaluator, mock_agent):
        """Test evaluating a single test case."""
        skip_if_no_evaluation_module()
        skip_if_no_strands_evals()

        reports = evaluator.evaluate_single(
            mock_agent,
            input_text="What's your name?",
            expected_output="Agent should say its name or that it doesn't have one",
            evaluators=["Builtin.Helpfulness"],
        )

        assert len(reports) == 1
        report = reports[0]
        assert hasattr(report, "overall_score")
        assert hasattr(report, "test_passes")

    def test_evaluate_multiple_cases(self, evaluator, mock_agent):
        """Test evaluating multiple test cases."""
        skip_if_no_evaluation_module()
        skip_if_no_strands_evals()

        reports = evaluator.evaluate_prompt(
            mock_agent,
            cases=QUICK_EVAL_CASES[:2],  # Use just 2 cases for speed
            evaluators=["Builtin.Helpfulness"],
        )

        assert len(reports) == 1
        report = reports[0]
        assert report is not None

    @pytest.mark.slow
    def test_prompt_helpfulness(self, evaluator, mock_agent):
        """Test that agent's prompt produces helpful responses."""
        skip_if_no_evaluation_module()
        skip_if_no_strands_evals()

        reports = evaluator.evaluate_prompt(
            mock_agent,
            cases=IDENTITY_TEST_CASES[:2],  # Use subset for speed
            evaluators=["Builtin.Helpfulness", "Builtin.Relevance"],
        )

        assert len(reports) == 2

        # Check scores are reasonable (will be low for mock agent, but should exist)
        for report in reports:
            assert 0.0 <= report.overall_score <= 1.0

    @pytest.mark.slow
    def test_multiple_evaluators(self, evaluator, mock_agent):
        """Test running multiple evaluators on same cases."""
        skip_if_no_evaluation_module()
        skip_if_no_strands_evals()

        evaluators_list = ["Builtin.Helpfulness", "Builtin.Relevance", "Builtin.Accuracy"]

        reports = evaluator.evaluate_prompt(
            mock_agent,
            cases=[QUICK_EVAL_CASES[0]],
            evaluators=evaluators_list,
        )

        assert len(reports) == len(evaluators_list)

    def test_evaluation_with_telemetry_reset(self, evaluator, mock_agent):
        """Test that telemetry reset works between evaluations."""
        skip_if_no_evaluation_module()
        skip_if_no_strands_evals()

        # First evaluation
        evaluator.evaluate_single(
            mock_agent,
            "What's your name?",
            "Agent name",
            ["Builtin.Helpfulness"],
        )

        # Reset telemetry
        evaluator.reset_telemetry()

        # Second evaluation should start fresh
        reports = evaluator.evaluate_single(
            mock_agent,
            "Who are you?",
            "Agent description",
            ["Builtin.Helpfulness"],
        )

        assert len(reports) == 1


# =============================================================================
# Quick Smoke Tests
# =============================================================================

@pytest.mark.evaluation
def test_prompt_helpfulness_quick(evaluator, mock_agent):
    """Quick smoke test for prompt helpfulness.

    This test is designed for pre-commit hooks and quick validation.
    Uses a minimal set of test cases for fast execution.
    """
    skip_if_no_evaluation_module()
    skip_if_no_strands_evals()

    # Use just one case for speed
    single_case = [IDENTITY_TEST_CASES[0]]

    reports = evaluator.evaluate_prompt(
        mock_agent,
        cases=single_case,
        evaluators=["Builtin.Helpfulness"],
    )

    assert len(reports) == 1
    report = reports[0]

    # For a mock agent, we just check it returns a valid score
    # Real agents should be tested with higher thresholds
    assert 0.0 <= report.overall_score <= 1.0


# =============================================================================
# Test Case Validation
# =============================================================================

class TestTestCases:
    """Validate that test cases are properly defined."""

    def test_identity_cases_exist(self):
        """Test that identity test cases are defined."""
        assert len(IDENTITY_TEST_CASES) >= 1
        for case in IDENTITY_TEST_CASES:
            assert hasattr(case, "input")
            assert hasattr(case, "expected_output")

    def test_memory_cases_exist(self):
        """Test that memory test cases are defined."""
        assert len(MEMORY_TEST_CASES) >= 1
        for case in MEMORY_TEST_CASES:
            assert hasattr(case, "input")
            assert hasattr(case, "expected_output")

    def test_skill_usage_cases_exist(self):
        """Test that skill usage test cases are defined."""
        assert len(SKILL_USAGE_TEST_CASES) >= 1
        for case in SKILL_USAGE_TEST_CASES:
            assert hasattr(case, "input")
            assert hasattr(case, "expected_output")

    def test_general_behavior_cases_exist(self):
        """Test that general behavior test cases are defined."""
        assert len(GENERAL_BEHAVIOR_TEST_CASES) >= 1
        for case in GENERAL_BEHAVIOR_TEST_CASES:
            assert hasattr(case, "input")
            assert hasattr(case, "expected_output")

    def test_quick_eval_cases_exist(self):
        """Test that quick eval cases are defined."""
        assert len(QUICK_EVAL_CASES) >= 1
        assert len(QUICK_EVAL_CASES) <= 5  # Should be small for quick tests
