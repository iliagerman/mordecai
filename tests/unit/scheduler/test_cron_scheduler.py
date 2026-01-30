"""Unit tests for Cron Scheduler.

Tests verify:
- Property 6: Post-Execution State Updates

Requirements: 2.5, 6.4, 6.5
"""

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from hypothesis import given, settings, strategies as st

from app.dao.cron_dao import CronDAO
from app.dao.cron_lock_dao import CronLockDAO
from app.dao.user_dao import UserDAO
from app.database import Database
from app.scheduler.cron_scheduler import CronScheduler
from app.services.cron_service import CronService


@pytest_asyncio.fixture
async def test_db():
    """Create a fresh in-memory database for each test."""
    db = Database("sqlite+aiosqlite:///:memory:")
    await db.init_db()
    yield db
    await db.close()


@pytest_asyncio.fixture
async def user_dao(test_db: Database) -> UserDAO:
    """Create UserDAO instance."""
    return UserDAO(test_db)


@pytest_asyncio.fixture
async def cron_dao(test_db: Database) -> CronDAO:
    """Create CronDAO instance."""
    return CronDAO(test_db)


@pytest_asyncio.fixture
async def lock_dao(test_db: Database) -> CronLockDAO:
    """Create CronLockDAO instance."""
    return CronLockDAO(test_db)


# Strategy for generating valid cron expressions
@st.composite
def valid_cron_expression(draw):
    """Generate valid 5-field cron expressions."""
    minute = draw(st.integers(min_value=0, max_value=59))
    hour = draw(st.integers(min_value=0, max_value=23))
    return f"{minute} {hour} * * *"


class TestProperty6PostExecutionStateUpdates:
    """Property 6: Post-Execution State Updates.

    *For any* successfully executed cron task, after execution completes:
    the lock should be released, last_executed_at should be set to the
    execution time, and next_execution_at should be recalculated based
    on the cron expression.

    **Validates: Requirements 2.5, 6.4, 6.5**
    """

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        cron_expression=valid_cron_expression(),
        task_name=st.text(
            min_size=1, max_size=30, alphabet=st.characters(
                whitelist_categories=("L", "N"),
                whitelist_characters="_-"
            )
        ).filter(lambda x: x.strip()),
    )
    async def test_post_execution_state_updates(
        self,
        cron_expression: str,
        task_name: str,
    ):
        """Feature: cron-job-scheduler, Property 6: Post-Execution.

        For any successfully executed cron task:
        1. Lock should be released
        2. last_executed_at should be set
        3. next_execution_at should be recalculated
        """
        db = Database("sqlite+aiosqlite:///:memory:")
        await db.init_db()

        try:
            user_dao = UserDAO(db)
            cron_dao = CronDAO(db)
            lock_dao = CronLockDAO(db)

            # Create user
            user_id = str(uuid.uuid4())
            await user_dao.create(user_id, f"test_{uuid.uuid4().hex[:8]}")

            # Create a task that is due (next_execution_at in the past)
            past_time = datetime.utcnow() - timedelta(hours=1)
            task = await cron_dao.create(
                user_id=user_id,
                name=task_name,
                instructions="Test instructions",
                cron_expression=cron_expression,
                next_execution_at=past_time,
            )

            # Record original state
            original_last_executed = task.last_executed_at
            original_next_execution = task.next_execution_at

            # Create mock agent service that returns a result
            mock_agent_service = AsyncMock()
            mock_agent_service.process_message = AsyncMock(
                return_value="Task executed successfully"
            )

            # Create cron service
            cron_service = CronService(
                cron_dao=cron_dao,
                lock_dao=lock_dao,
                agent_service=mock_agent_service,
            )

            # Create scheduler
            instance_id = str(uuid.uuid4())
            scheduler = CronScheduler(
                cron_service=cron_service,
                lock_dao=lock_dao,
                agent_service=mock_agent_service,
                instance_id=instance_id,
            )

            # Execute the task
            await scheduler._execute_task_with_lock(task)

            # Verify post-execution state

            # 1. Lock should be released
            is_locked = await lock_dao.is_locked(task.id)
            assert is_locked is False, "Lock should be released"

            # 2. last_executed_at should be set (not None anymore)
            updated_task = await cron_dao.get_by_id(task.id)
            assert updated_task is not None
            assert updated_task.last_executed_at is not None, (
                "last_executed_at should be set"
            )
            assert updated_task.last_executed_at != original_last_executed

            # 3. next_execution_at should be recalculated (in the future)
            assert updated_task.next_execution_at > original_next_execution, (
                "next_execution_at should be recalculated"
            )
            assert updated_task.next_execution_at > datetime.utcnow(), (
                "next_execution_at should be in the future"
            )

        finally:
            await db.close()

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        cron_expression=valid_cron_expression(),
    )
    async def test_lock_released_on_execution_failure(
        self,
        cron_expression: str,
    ):
        """Feature: cron-job-scheduler, Property 6: Post-Execution.

        For any cron task where execution fails, the lock should still
        be released to allow retry on next scheduler run.
        """
        db = Database("sqlite+aiosqlite:///:memory:")
        await db.init_db()

        try:
            user_dao = UserDAO(db)
            cron_dao = CronDAO(db)
            lock_dao = CronLockDAO(db)

            # Create user
            user_id = str(uuid.uuid4())
            await user_dao.create(user_id, f"test_{uuid.uuid4().hex[:8]}")

            # Create a task that is due
            past_time = datetime.utcnow() - timedelta(hours=1)
            task = await cron_dao.create(
                user_id=user_id,
                name="failing_task",
                instructions="Test instructions",
                cron_expression=cron_expression,
                next_execution_at=past_time,
            )

            # Create mock agent service that raises an exception
            mock_agent_service = AsyncMock()
            mock_agent_service.process_message = AsyncMock(
                side_effect=Exception("Agent execution failed")
            )

            # Create cron service
            cron_service = CronService(
                cron_dao=cron_dao,
                lock_dao=lock_dao,
                agent_service=mock_agent_service,
            )

            # Create scheduler
            instance_id = str(uuid.uuid4())
            scheduler = CronScheduler(
                cron_service=cron_service,
                lock_dao=lock_dao,
                agent_service=mock_agent_service,
                instance_id=instance_id,
            )

            # Execute the task (should not raise, but handle error internally)
            await scheduler._execute_task_with_lock(task)

            # Verify lock is released even on failure
            is_locked = await lock_dao.is_locked(task.id)
            assert is_locked is False, (
                "Lock should be released on failure"
            )

        finally:
            await db.close()

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        cron_expression=valid_cron_expression(),
    )
    async def test_next_execution_matches_cron_pattern(
        self,
        cron_expression: str,
    ):
        """Feature: cron-job-scheduler, Property 6: Post-Execution.

        For any successfully executed cron task, the recalculated
        next_execution_at should match the cron pattern.
        """
        from croniter import croniter

        db = Database("sqlite+aiosqlite:///:memory:")
        await db.init_db()

        try:
            user_dao = UserDAO(db)
            cron_dao = CronDAO(db)
            lock_dao = CronLockDAO(db)

            # Create user
            user_id = str(uuid.uuid4())
            await user_dao.create(user_id, f"test_{uuid.uuid4().hex[:8]}")

            # Create a task that is due
            past_time = datetime.utcnow() - timedelta(hours=1)
            task = await cron_dao.create(
                user_id=user_id,
                name="pattern_test_task",
                instructions="Test instructions",
                cron_expression=cron_expression,
                next_execution_at=past_time,
            )

            # Create mock agent service
            mock_agent_service = AsyncMock()
            mock_agent_service.process_message = AsyncMock(
                return_value="Success"
            )

            # Create cron service
            cron_service = CronService(
                cron_dao=cron_dao,
                lock_dao=lock_dao,
                agent_service=mock_agent_service,
            )

            # Create scheduler
            instance_id = str(uuid.uuid4())
            scheduler = CronScheduler(
                cron_service=cron_service,
                lock_dao=lock_dao,
                agent_service=mock_agent_service,
                instance_id=instance_id,
            )

            # Execute the task
            await scheduler._execute_task_with_lock(task)

            # Get updated task
            updated_task = await cron_dao.get_by_id(task.id)
            assert updated_task is not None

            # Verify next_execution_at matches the cron pattern
            # by checking that croniter agrees it's a valid match
            cron = croniter(
                cron_expression,
                updated_task.next_execution_at - timedelta(seconds=1)
            )
            expected_next = cron.get_next(datetime)

            # Stored next_execution_at should equal what croniter calculates
            assert updated_task.next_execution_at == expected_next, (
                "next_execution_at should match the cron pattern"
            )

        finally:
            await db.close()
