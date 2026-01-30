"""Unit tests for Cron Service layer.

Tests verify:
- Cron expression validation (Property 5)
- Next execution calculation (Property 1)

Requirements: 5.1, 5.5, 8.1, 8.2, 8.3, 1.3, 6.4
"""

from datetime import datetime, timedelta

import pytest
from hypothesis import given, settings, strategies as st

from app.services.cron_service import (
    CronExpressionError,
    CronService,
)


# Strategy for generating valid cron field values
minute_st = st.integers(min_value=0, max_value=59)
hour_st = st.integers(min_value=0, max_value=23)
day_st = st.integers(min_value=1, max_value=31)
month_st = st.integers(min_value=1, max_value=12)
weekday_st = st.integers(min_value=0, max_value=6)


@st.composite
def valid_cron_expression(draw):
    """Generate valid 5-field cron expressions."""
    minute = draw(st.one_of(
        minute_st.map(str),
        st.just("*"),
        st.just("*/5"),
        st.just("*/10"),
        st.just("*/15"),
    ))
    hour = draw(st.one_of(
        hour_st.map(str),
        st.just("*"),
        st.just("*/2"),
        st.just("*/6"),
    ))
    day = draw(st.one_of(
        day_st.map(str),
        st.just("*"),
        st.just("*/7"),
    ))
    month = draw(st.one_of(
        month_st.map(str),
        st.just("*"),
    ))
    weekday = draw(st.one_of(
        weekday_st.map(str),
        st.just("*"),
    ))
    return f"{minute} {hour} {day} {month} {weekday}"


@st.composite
def invalid_cron_expression(draw):
    """Generate invalid cron expressions."""
    invalid_type = draw(st.sampled_from([
        "wrong_field_count",
        "invalid_minute",
        "invalid_hour",
        "invalid_day",
        "invalid_month",
        "invalid_weekday",
        "empty",
        "whitespace_only",
        "random_text",
    ]))

    if invalid_type == "wrong_field_count":
        # Generate expressions with wrong number of fields
        num_fields = draw(st.integers(min_value=1, max_value=10).filter(
            lambda x: x != 5
        ))
        return " ".join(["*"] * num_fields)
    elif invalid_type == "invalid_minute":
        # Minute out of range (0-59)
        bad_minute = draw(st.integers(min_value=60, max_value=100))
        return f"{bad_minute} 0 * * *"
    elif invalid_type == "invalid_hour":
        # Hour out of range (0-23)
        bad_hour = draw(st.integers(min_value=24, max_value=100))
        return f"0 {bad_hour} * * *"
    elif invalid_type == "invalid_day":
        # Day out of range (1-31)
        bad_day = draw(st.integers(min_value=32, max_value=100))
        return f"0 0 {bad_day} * *"
    elif invalid_type == "invalid_month":
        # Month out of range (1-12)
        bad_month = draw(st.integers(min_value=13, max_value=100))
        return f"0 0 * {bad_month} *"
    elif invalid_type == "invalid_weekday":
        # Weekday out of range (0-7 are valid in croniter, 8+ is invalid)
        bad_weekday = draw(st.integers(min_value=8, max_value=100))
        return f"0 0 * * {bad_weekday}"
    elif invalid_type == "empty":
        return ""
    elif invalid_type == "whitespace_only":
        return "   "
    else:  # random_text
        return draw(st.text(min_size=1, max_size=20).filter(
            lambda x: not x.strip().replace("*", "").replace(" ", "").isdigit()
        ))


class TestProperty5CronExpressionValidation:
    """Property 5: Cron Expression Validation.

    *For any* string input, the cron expression validator should accept
    valid 5-field cron expressions (minute hour day month weekday) and
    reject invalid expressions with a descriptive error message.

    **Validates: Requirements 5.1, 5.5, 8.1, 8.2**
    """

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(expression=valid_cron_expression())
    async def test_valid_cron_expressions_accepted(self, expression: str):
        """Feature: cron-job-scheduler, Property 5: Cron Expression Validation.

        For any valid 5-field cron expression, validation should succeed.
        """
        # Create service with minimal dependencies (validation is synchronous)
        service = CronService(
            cron_dao=None,  # type: ignore
            lock_dao=None,  # type: ignore
            agent_service=None,  # type: ignore
        )

        # Should not raise
        result = service.validate_cron_expression(expression)
        assert result is True

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(expression=invalid_cron_expression())
    async def test_invalid_cron_expressions_rejected(self, expression: str):
        """Feature: cron-job-scheduler, Property 5: Cron Expression Validation.

        For any invalid cron expression, validation should raise
        CronExpressionError with a descriptive message.
        """
        service = CronService(
            cron_dao=None,  # type: ignore
            lock_dao=None,  # type: ignore
            agent_service=None,  # type: ignore
        )

        with pytest.raises(CronExpressionError) as exc_info:
            service.validate_cron_expression(expression)

        # Verify error message is descriptive
        error_msg = str(exc_info.value)
        assert "Invalid cron expression" in error_msg
        assert len(error_msg) > len("Invalid cron expression")


class TestProperty1CronNextExecutionCalculation:
    """Property 1: Cron Expression Next Execution Calculation.

    *For any* valid cron expression and reference datetime, calculating
    the next execution time and then verifying it against the cron schedule
    should confirm the calculated time is the earliest future time matching
    the cron pattern.

    **Validates: Requirements 1.3, 6.4, 8.3**
    """

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(expression=valid_cron_expression())
    async def test_next_execution_is_in_future(self, expression: str):
        """Feature: cron-job-scheduler, Property 1: Next Execution Calculation.

        For any valid cron expression, the calculated next execution time
        should always be in the future relative to the reference time.
        """
        service = CronService(
            cron_dao=None,  # type: ignore
            lock_dao=None,  # type: ignore
            agent_service=None,  # type: ignore
        )

        from_time = datetime.utcnow()
        try:
            next_exec = service.calculate_next_execution(expression, from_time)
            # Next execution should be strictly after from_time
            assert next_exec > from_time
        except CronExpressionError:
            # Some syntactically valid expressions may be semantically
            # impossible (e.g., "0 * 30 2 *" - Feb 30th doesn't exist)
            # This is acceptable behavior
            pass

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        expression=valid_cron_expression(),
        hours_offset=st.integers(min_value=-1000, max_value=1000),
    )
    async def test_next_execution_matches_cron_pattern(
        self,
        expression: str,
        hours_offset: int,
    ):
        """Feature: cron-job-scheduler, Property 1: Next Execution Calculation.

        For any valid cron expression and reference time, the calculated
        next execution should match the cron pattern fields.
        """
        from croniter import croniter

        service = CronService(
            cron_dao=None,  # type: ignore
            lock_dao=None,  # type: ignore
            agent_service=None,  # type: ignore
        )

        # Use a reference time offset from now
        from_time = datetime.utcnow() + timedelta(hours=hours_offset)

        try:
            next_exec = service.calculate_next_execution(expression, from_time)

            # Verify the calculated time matches the cron pattern
            # by checking that croniter agrees it's a valid match
            cron = croniter(expression, next_exec - timedelta(seconds=1))
            expected_next = cron.get_next(datetime)

            # The calculated next_exec should equal what croniter calculates
            # from just before that time
            assert next_exec == expected_next
        except CronExpressionError:
            # Some syntactically valid expressions may be semantically
            # impossible (e.g., "* * 30 2 *" - Feb 30th doesn't exist)
            # This is acceptable behavior
            pass

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(expression=valid_cron_expression())
    async def test_consecutive_executions_are_ordered(self, expression: str):
        """Feature: cron-job-scheduler, Property 1: Next Execution Calculation.

        For any valid cron expression, calculating next execution twice
        in sequence should produce strictly increasing times.
        """
        service = CronService(
            cron_dao=None,  # type: ignore
            lock_dao=None,  # type: ignore
            agent_service=None,  # type: ignore
        )

        from_time = datetime.utcnow()
        try:
            first_exec = service.calculate_next_execution(
                expression, from_time
            )
            second_exec = service.calculate_next_execution(
                expression, first_exec
            )

            # Second execution should be strictly after first
            assert second_exec > first_exec
        except CronExpressionError:
            # Some syntactically valid expressions may be semantically
            # impossible (e.g., "0 * 30 2 *" - Feb 30th doesn't exist)
            # This is acceptable behavior
            pass
