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
from croniter import croniter

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


class TestProperty6PostExecutionStateUpdates:
    """Property 6: Post-Execution State Updates.

    *For any* successfully executed cron task, after execution completes:
    the lock should be released, last_executed_at should be set to the
    execution time, and next_execution_at should be recalculated based
    on the cron expression.

    **Validates: Requirements 2.5, 6.4, 6.5**
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "cron_expression,task_name",
        [
            ("0 0 * * *", "task_midnight"),
            ("15 3 * * *", "task_0315"),
            ("59 23 * * *", "task_2359"),
        ],
    )
    async def test_post_execution_state_updates(
        self,
        test_db: Database,
        cron_expression: str,
        task_name: str,
    ):
        """Feature: cron-job-scheduler, Property 6: Post-Execution.

        For any successfully executed cron task:
        1. Lock should be released
        2. last_executed_at should be set
        3. next_execution_at should be recalculated
        """
        user_dao = UserDAO(test_db)
        cron_dao = CronDAO(test_db)
        lock_dao = CronLockDAO(test_db)

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

        original_last_executed = task.last_executed_at
        original_next_execution = task.next_execution_at

        # Create mock agent service that returns a result
        mock_agent_service = AsyncMock()
        mock_agent_service.process_message = AsyncMock(return_value="Task executed successfully")

        # Create cron service
        cron_service = CronService(
            cron_dao=cron_dao,
            lock_dao=lock_dao,
            agent_service=mock_agent_service,
        )

        # Create scheduler
        scheduler = CronScheduler(
            cron_service=cron_service,
            lock_dao=lock_dao,
            agent_service=mock_agent_service,
            instance_id=str(uuid.uuid4()),
        )

        # Execute the task
        await scheduler._execute_task_with_lock(task)

        # 1) Lock should be released
        assert (await lock_dao.is_locked(task.id)) is False, "Lock should be released"

        # 2) last_executed_at should be set
        updated_task = await cron_dao.get_by_id(task.id)
        assert updated_task is not None
        assert updated_task.last_executed_at is not None, "last_executed_at should be set"
        assert updated_task.last_executed_at != original_last_executed

        # 3) next_execution_at should be recalculated and consistent with croniter
        assert updated_task.next_execution_at > original_next_execution
        assert updated_task.next_execution_at > updated_task.last_executed_at

        expected_next = croniter(
            cron_expression,
            updated_task.last_executed_at,
        ).get_next(datetime)
        assert updated_task.next_execution_at == expected_next

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "cron_expression",
        [
            ("0 0 * * *"),
            ("15 3 * * *"),
            ("59 23 * * *"),
        ],
    )
    async def test_lock_released_on_execution_failure(
        self,
        test_db: Database,
        cron_expression: str,
    ):
        """Feature: cron-job-scheduler, Property 6: Post-Execution.

        For any cron task where execution fails, the lock should still
        be released to allow retry on next scheduler run.
        """
        user_dao = UserDAO(test_db)
        cron_dao = CronDAO(test_db)
        lock_dao = CronLockDAO(test_db)

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

        original_last_executed = task.last_executed_at
        original_next_execution = task.next_execution_at

        # Create mock agent service that raises an exception
        mock_agent_service = AsyncMock()
        mock_agent_service.process_cron_task = AsyncMock(
            side_effect=Exception("Agent execution failed")
        )

        # Create cron service
        cron_service = CronService(
            cron_dao=cron_dao,
            lock_dao=lock_dao,
            agent_service=mock_agent_service,
        )

        # Create scheduler
        scheduler = CronScheduler(
            cron_service=cron_service,
            lock_dao=lock_dao,
            agent_service=mock_agent_service,
            instance_id=str(uuid.uuid4()),
        )

        # Execute the task (should not raise, scheduler handles internally)
        await scheduler._execute_task_with_lock(task)

        # Verify lock is released even on failure
        assert (await lock_dao.is_locked(task.id)) is False, "Lock should be released on failure"

        # On failure, timestamps should not be updated
        updated_task = await cron_dao.get_by_id(task.id)
        assert updated_task is not None
        assert updated_task.last_executed_at == original_last_executed
        assert updated_task.next_execution_at == original_next_execution
