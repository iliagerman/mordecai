"""Evaluation framework for Mordecai agents using Strands Evals + AgentCore.

This module provides the AgentEvaluator class which wraps Strands Evals
and AgentCore Evaluation API to test agent behavior, prompt effectiveness,
and skill usage patterns.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from strands_evals import Case, Experiment, Evaluator

logger = logging.getLogger(__name__)


class AgentEvaluator:
    """Evaluates Mordecai agents using Strands Evals + AgentCore.

    This class sets up telemetry, creates evaluators, and runs
    evaluation experiments against test cases.

    Example:
        evaluator = AgentEvaluator(region="us-west-2")

        cases = [
            Case(input="What's your name?", expected_output="Agent name or no name"),
        ]

        reports = evaluator.evaluate_prompt(
            agent,
            cases,
            evaluators=["Builtin.Helpfulness", "Builtin.Relevance"]
        )
    """

    def __init__(self, config: Any | None = None, region: str | None = None):
        """Initialize the evaluator.

        Args:
            config: Optional AgentConfig for AWS region settings.
            region: AWS region for AgentCore Evaluation API.
                    If not provided, uses AWS_REGION env var or "us-west-2".
        """
        self.config = config

        # Determine region
        if region is None and config is not None:
            region = getattr(config, "aws_region", None)
        self._region = region or "us-west-2"

        # Setup telemetry (lazy, will be initialized when needed)
        self._telemetry = None

    @property
    def telemetry(self):
        """Get or create the telemetry instance."""
        if self._telemetry is None:
            from strands_evals.telemetry import StrandsEvalsTelemetry

            self._telemetry = StrandsEvalsTelemetry().setup_in_memory_exporter()
            logger.info("Initialized StrandsEvalsTelemetry with in-memory exporter")
        return self._telemetry

    def create_evaluator(
        self,
        evaluator_name: str,
        *,
        test_pass_score: float = 0.7,
        region: str | None = None,
    ) -> Evaluator:
        """Create an AgentCore evaluator.

        Args:
            evaluator_name: Built-in evaluator name (e.g., "Builtin.Helpfulness")
                          or custom evaluator ARN.
            test_pass_score: Minimum score for test to pass (0.0-1.0). Default: 0.7.
            region: AWS region. Defaults to instance region.

        Returns:
            A Strands-compatible evaluator instance.

        Raises:
            ImportError: If bedrock_agentcore evaluation module is not available.
        """
        try:
            from bedrock_agentcore.evaluation import create_strands_evaluator
        except ImportError as e:
            raise ImportError(
                "bedrock_agentcore evaluation module not available. "
                "Install with: pip install 'bedrock-agentcore[strands-agents-evals]'"
            ) from e

        eval_region = region or self._region

        return create_strands_evaluator(
            evaluator_name,
            region=eval_region,
            test_pass_score=test_pass_score,
        )

    def evaluate_prompt(
        self,
        agent: Any,
        cases: list[Case],
        evaluators: list[str] | list[Evaluator],
        *,
        test_pass_score: float = 0.7,
        region: str | None = None,
    ) -> list[Any]:
        """Evaluate agent with given prompt against test cases.

        Args:
            agent: The Strands Agent instance to evaluate.
            cases: List of test cases (strands_evals.Case).
            evaluators: List of evaluator names or Evaluator instances.
            test_pass_score: Default minimum score for evaluators created from names.
            region: AWS region for evaluators.

        Returns:
            List of evaluation reports from each evaluator.

        Example:
            cases = [
                Case(input="What's your name?", expected_output="..."),
            ]
            reports = evaluator.evaluate_prompt(
                agent,
                cases,
                evaluators=["Builtin.Helpfulness"]
            )
        """
        from strands_evals import Experiment

        # Create evaluator instances from names if needed
        evaluator_instances: list[Evaluator] = []
        for eval_config in evaluators:
            if isinstance(eval_config, str):
                evaluator_instances.append(
                    self.create_evaluator(eval_config, test_pass_score=test_pass_score, region=region)
                )
            else:
                evaluator_instances.append(eval_config)

        # Create task function that captures telemetry spans
        def task_fn(case: Case) -> dict[str, Any]:
            # Run the agent
            response = agent(case.input)

            # Get raw spans from telemetry exporter
            # Note: Convert tuple to list to avoid Pydantic serialization warning
            raw_spans = list(self.telemetry.in_memory_exporter.get_finished_spans())

            return {
                "output": str(response),
                "trajectory": raw_spans,  # Raw OTel spans - auto-converted to ADOT
            }

        # Create and run experiment
        experiment = Experiment(cases=cases, evaluators=evaluator_instances)
        reports = experiment.run_evaluations(task_fn)

        # Log results
        for report in reports:
            pass_rate = sum(report.test_passes) / len(report.test_passes) if report.test_passes else 0
            logger.info(
                "Evaluator %s: score=%.2f, pass_rate=%.1f%%",
                report.evaluator_id,
                report.overall_score,
                pass_rate * 100,
            )

        return reports

    def evaluate_single(
        self,
        agent: Any,
        input_text: str,
        expected_output: str,
        evaluators: list[str] | list[Evaluator],
        *,
        test_pass_score: float = 0.7,
        region: str | None = None,
    ) -> list[Any]:
        """Evaluate agent against a single test case.

        Convenience method for evaluating a single input/output pair.

        Args:
            agent: The Strands Agent instance to evaluate.
            input_text: Input text to send to the agent.
            expected_output: Expected output description.
            evaluators: List of evaluator names or Evaluator instances.
            test_pass_score: Default minimum score for evaluators created from names.
            region: AWS region for evaluators.

        Returns:
            List of evaluation reports from each evaluator.
        """
        from strands_evals import Case

        case = Case(input=input_text, expected_output=expected_output)
        return self.evaluate_prompt(agent, [case], evaluators, test_pass_score=test_pass_score, region=region)

    def reset_telemetry(self) -> None:
        """Reset the telemetry exporter, clearing all captured spans.

        Call this between independent test runs to avoid span contamination.
        """
        if self._telemetry is not None:
            self._telemetry = None
        logger.debug("Telemetry reset")


class EvaluationMetrics:
    """Container for evaluation metrics and results.

    Provides helper methods for analyzing evaluation reports.
    """

    @staticmethod
    def get_pass_rate(report: Any) -> float:
        """Calculate the pass rate from an evaluation report.

        Args:
            report: Evaluation report from Strands Evals.

        Returns:
            Pass rate as a float between 0.0 and 1.0.
        """
        if not report.test_passes:
            return 0.0
        return sum(report.test_passes) / len(report.test_passes)

    @staticmethod
    def format_report(report: Any) -> str:
        """Format an evaluation report for logging/display.

        Args:
            report: Evaluation report from Strands Evals.

        Returns:
            Formatted string representation of the report.
        """
        pass_rate = EvaluationMetrics.get_pass_rate(report)
        passed = sum(report.test_passes) if report.test_passes else 0
        total = len(report.test_passes) if report.test_passes else 0

        return (
            f"Evaluator: {report.evaluator_id}\n"
            f"  Overall Score: {report.overall_score:.2f}\n"
            f"  Pass Rate: {pass_rate:.1%} ({passed}/{total} tests passed)"
        )

    @staticmethod
    def check_threshold(
        report: Any,
        min_score: float = 0.7,
        min_pass_rate: float = 0.7,
    ) -> tuple[bool, str]:
        """Check if a report meets minimum thresholds.

        Args:
            report: Evaluation report from Strands Evals.
            min_score: Minimum overall score required.
            min_pass_rate: Minimum pass rate required.

        Returns:
            Tuple of (passed, message).
        """
        score_ok = report.overall_score >= min_score
        pass_rate = EvaluationMetrics.get_pass_rate(report)
        pass_rate_ok = pass_rate >= min_pass_rate

        if score_ok and pass_rate_ok:
            return True, "Passed"
        if not score_ok and not pass_rate_ok:
            return False, f"Failed: score {report.overall_score:.2f} < {min_score}, pass rate {pass_rate:.1%} < {min_pass_rate:.1%}"
        if not score_ok:
            return False, f"Failed: score {report.overall_score:.2f} < {min_score}"
        return False, f"Failed: pass rate {pass_rate:.1%} < {min_pass_rate:.1%}"


def create_evaluation_fixture(
    config: Any | None = None,
    region: str | None = None,
) -> Callable[[], AgentEvaluator]:
    """Create a pytest fixture for AgentEvaluator.

    Args:
        config: Optional AgentConfig.
        region: AWS region for evaluators.

    Returns:
        Fixture function that yields an AgentEvaluator instance.

    Example in conftest.py:
        @pytest.fixture
        def evaluator():
            from tests.evaluation.eval_framework import create_evaluation_fixture
            return create_evaluation_fixture()()
    """
    def _fixture() -> AgentEvaluator:
        return AgentEvaluator(config=config, region=region)

    return _fixture
