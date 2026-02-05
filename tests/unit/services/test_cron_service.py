"""Unit tests for Cron Service layer.

Tests verify:
- Cron expression validation (Property 5)
- Next execution calculation (Property 1)

Requirements: 5.1, 5.5, 8.1, 8.2, 8.3, 1.3, 6.4
"""

from datetime import datetime, timedelta

import pytest

from app.services.cron_service import (
    CronExpressionError,
    CronService,
)

VALID_CRON_EXPRESSIONS = [
    "* * * * *",
    "0 6 * * *",
    "*/5 * * * *",
    "15 */2 * * *",
    "0 0 */7 * *",
]

INVALID_CRON_EXPRESSIONS = [
    "",
    "   ",
    "* * * *",  # wrong field count
    "* * * * * *",  # wrong field count
    "60 0 * * *",  # invalid minute
    "0 24 * * *",  # invalid hour
    "0 0 32 * *",  # invalid day
    "0 0 * 13 *",  # invalid month
    "0 0 * * 8",  # invalid weekday
    "hello world",
]


@pytest.fixture
def cron_service() -> CronService:
    return CronService(
        cron_dao=None,  # type: ignore
        lock_dao=None,  # type: ignore
        agent_service=None,  # type: ignore
    )


@pytest.mark.parametrize("expression", VALID_CRON_EXPRESSIONS)
def test_valid_cron_expressions_accepted(cron_service: CronService, expression: str):
    assert cron_service.validate_cron_expression(expression) is True


@pytest.mark.parametrize("expression", INVALID_CRON_EXPRESSIONS)
def test_invalid_cron_expressions_rejected(cron_service: CronService, expression: str):
    with pytest.raises(CronExpressionError) as exc_info:
        cron_service.validate_cron_expression(expression)
    assert "Invalid cron expression" in str(exc_info.value)


@pytest.mark.parametrize("expression", ["* * * * *", "0 6 * * *", "*/5 * * * *"])
def test_next_execution_is_in_future(cron_service: CronService, expression: str):
    from_time = datetime.utcnow()
    next_exec = cron_service.calculate_next_execution(expression, from_time)
    assert next_exec > from_time


@pytest.mark.parametrize("hours_offset", [-48, -1, 0, 1, 48])
def test_next_execution_matches_croniter(cron_service: CronService, hours_offset: int):
    from croniter import croniter

    expression = "*/5 * * * *"
    from_time = datetime.utcnow() + timedelta(hours=hours_offset)

    next_exec = cron_service.calculate_next_execution(expression, from_time)
    cron = croniter(expression, next_exec - timedelta(seconds=1))
    expected_next = cron.get_next(datetime)
    assert next_exec == expected_next


@pytest.mark.parametrize("expression", ["* * * * *", "0 6 * * *"])
def test_consecutive_executions_are_ordered(cron_service: CronService, expression: str):
    from_time = datetime.utcnow()
    first_exec = cron_service.calculate_next_execution(expression, from_time)
    second_exec = cron_service.calculate_next_execution(expression, first_exec)
    assert second_exec > first_exec
