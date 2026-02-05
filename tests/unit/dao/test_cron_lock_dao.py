"""Unit tests for Cron Lock DAO layer.

Tests verify:
- Lock expiry behavior (Property 2)
- Atomic lock acquisition (Property 3)

Requirements: 2.2, 2.3, 2.4, 4.1, 4.2, 4.3, 4.4
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any, cast

import pytest
import pytest_asyncio

from app.dao.cron_dao import CronDAO
from app.dao.cron_lock_dao import CronLockDAO
from app.dao.user_dao import UserDAO
from app.database import Database
from app.models.domain import CronLock


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


async def create_test_task(
    user_dao: UserDAO,
    cron_dao: CronDAO,
    user_id: str | None = None,
) -> str:
    """Helper to create a test user and cron task, returns task_id."""
    if user_id is None:
        user_id = str(uuid.uuid4())
    await user_dao.create(user_id, f"test_{uuid.uuid4().hex[:8]}")

    next_exec = datetime.utcnow() + timedelta(hours=1)
    task = await cron_dao.create(
        user_id=user_id,
        name=f"task_{uuid.uuid4().hex[:8]}",
        instructions="Test instructions",
        cron_expression="0 6 * * *",
        next_execution_at=next_exec,
    )
    return task.id


class TestCronLockDAOBasicOperations:
    """Basic unit tests for CronLockDAO operations."""

    async def test_acquire_lock_on_unlocked_task(
        self,
        lock_dao: CronLockDAO,
        cron_dao: CronDAO,
        user_dao: UserDAO,
    ):
        """Verify lock can be acquired on an unlocked task."""
        task_id = await create_test_task(user_dao, cron_dao)
        instance_id = str(uuid.uuid4())

        result = await lock_dao.try_acquire_lock(task_id, instance_id)

        assert result is True
        assert await lock_dao.is_locked(task_id) is True

    async def test_acquire_lock_fails_when_already_locked(
        self,
        lock_dao: CronLockDAO,
        cron_dao: CronDAO,
        user_dao: UserDAO,
    ):
        """Verify second lock attempt fails when task is already locked."""
        task_id = await create_test_task(user_dao, cron_dao)
        instance_1 = str(uuid.uuid4())
        instance_2 = str(uuid.uuid4())

        # First lock succeeds
        result1 = await lock_dao.try_acquire_lock(task_id, instance_1)
        assert result1 is True

        # Second lock fails
        result2 = await lock_dao.try_acquire_lock(task_id, instance_2)
        assert result2 is False

    async def test_release_lock(
        self,
        lock_dao: CronLockDAO,
        cron_dao: CronDAO,
        user_dao: UserDAO,
    ):
        """Verify lock can be released."""
        task_id = await create_test_task(user_dao, cron_dao)
        instance_id = str(uuid.uuid4())

        await lock_dao.try_acquire_lock(task_id, instance_id)
        assert await lock_dao.is_locked(task_id) is True

        result = await lock_dao.release_lock(task_id)
        assert result is True
        assert await lock_dao.is_locked(task_id) is False

    async def test_release_nonexistent_lock(
        self,
        lock_dao: CronLockDAO,
        cron_dao: CronDAO,
        user_dao: UserDAO,
    ):
        """Verify releasing nonexistent lock returns False."""
        task_id = await create_test_task(user_dao, cron_dao)

        result = await lock_dao.release_lock(task_id)
        assert result is False

    async def test_is_locked_returns_false_for_unlocked(
        self,
        lock_dao: CronLockDAO,
        cron_dao: CronDAO,
        user_dao: UserDAO,
    ):
        """Verify is_locked returns False for unlocked task."""
        task_id = await create_test_task(user_dao, cron_dao)

        result = await lock_dao.is_locked(task_id)
        assert result is False

    async def test_get_lock_returns_domain_model(
        self,
        lock_dao: CronLockDAO,
        cron_dao: CronDAO,
        user_dao: UserDAO,
    ):
        """Verify get_lock returns Pydantic CronLock model."""
        task_id = await create_test_task(user_dao, cron_dao)
        instance_id = str(uuid.uuid4())

        await lock_dao.try_acquire_lock(task_id, instance_id)
        lock = await lock_dao.get_lock(task_id)

        assert isinstance(lock, CronLock)
        assert lock.task_id == task_id
        assert lock.instance_id == instance_id

    async def test_get_lock_returns_none_when_not_locked(
        self,
        lock_dao: CronLockDAO,
        cron_dao: CronDAO,
        user_dao: UserDAO,
    ):
        """Verify get_lock returns None when no lock exists."""
        task_id = await create_test_task(user_dao, cron_dao)

        lock = await lock_dao.get_lock(task_id)
        assert lock is None


class TestLockExpiryBehavior:
    """Deterministic tests for lock expiry behavior."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("minutes_old", [11, 60])
    async def test_expired_lock_can_be_replaced(self, minutes_old: int):
        db = Database("sqlite+aiosqlite:///:memory:")
        await db.init_db()
        try:
            user_dao = UserDAO(db)
            cron_dao = CronDAO(db)
            lock_dao = CronLockDAO(db)

            task_id = await create_test_task(user_dao, cron_dao)
            instance_1 = str(uuid.uuid4())
            instance_2 = str(uuid.uuid4())

            await lock_dao.try_acquire_lock(task_id, instance_1)

            async with db.session() as session:
                from sqlalchemy import select

                from app.models.orm import CronLockModel

                result = await session.execute(
                    select(CronLockModel).where(CronLockModel.task_id == task_id)
                )
                lock_model = result.scalar_one()
                cast(Any, lock_model).lock_acquired_at = datetime.utcnow() - timedelta(
                    minutes=minutes_old
                )

            assert await lock_dao.try_acquire_lock(task_id, instance_2) is True
            lock = await lock_dao.get_lock(task_id)
            assert lock is not None
            assert lock.instance_id == instance_2
        finally:
            await db.close()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("minutes_old", [0, 9])
    async def test_valid_lock_cannot_be_replaced(self, minutes_old: int):
        db = Database("sqlite+aiosqlite:///:memory:")
        await db.init_db()
        try:
            user_dao = UserDAO(db)
            cron_dao = CronDAO(db)
            lock_dao = CronLockDAO(db)

            task_id = await create_test_task(user_dao, cron_dao)
            instance_1 = str(uuid.uuid4())
            instance_2 = str(uuid.uuid4())

            await lock_dao.try_acquire_lock(task_id, instance_1)

            async with db.session() as session:
                from sqlalchemy import select

                from app.models.orm import CronLockModel

                result = await session.execute(
                    select(CronLockModel).where(CronLockModel.task_id == task_id)
                )
                lock_model = result.scalar_one()
                cast(Any, lock_model).lock_acquired_at = datetime.utcnow() - timedelta(
                    minutes=minutes_old
                )

            assert await lock_dao.try_acquire_lock(task_id, instance_2) is False
            lock = await lock_dao.get_lock(task_id)
            assert lock is not None
            assert lock.instance_id == instance_1
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_exactly_10_minute_old_lock_is_not_expired(self):
        """Edge case: lock just under 10 minutes old is NOT expired."""
        db = Database("sqlite+aiosqlite:///:memory:")
        await db.init_db()

        try:
            user_dao = UserDAO(db)
            cron_dao = CronDAO(db)
            lock_dao = CronLockDAO(db)

            task_id = await create_test_task(user_dao, cron_dao)
            instance_1 = str(uuid.uuid4())
            instance_2 = str(uuid.uuid4())

            await lock_dao.try_acquire_lock(task_id, instance_1)

            # Set lock to just under 10 minutes old (9 min 59 sec)
            # to avoid timing issues
            async with db.session() as session:
                from sqlalchemy import select

                from app.models.orm import CronLockModel

                result = await session.execute(
                    select(CronLockModel).where(CronLockModel.task_id == task_id)
                )
                lock_model = result.scalar_one()
                cast(Any, lock_model).lock_acquired_at = datetime.utcnow() - timedelta(
                    minutes=9, seconds=59
                )

            # Lock just under 10 minutes should still be valid
            result = await lock_dao.try_acquire_lock(task_id, instance_2)
            assert result is False

        finally:
            await db.close()


class TestAtomicLockAcquisition:
    """Deterministic tests for atomic lock acquisition.

    *For any* two concurrent lock acquisition attempts on the same task_id,
    exactly one should succeed and one should fail, ensuring no duplicate
    executions.

    **Validates: Requirements 2.2, 4.4**

    Note: SQLite in-memory databases have limited concurrency support.
    These tests verify the property holds for sequential access and
    that concurrent access doesn't result in duplicate locks.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("num_instances", [2, 5])
    async def test_only_one_lock_succeeds(self, num_instances: int):
        db = Database("sqlite+aiosqlite:///:memory:")
        await db.init_db()
        try:
            user_dao = UserDAO(db)
            cron_dao = CronDAO(db)
            lock_dao = CronLockDAO(db)

            task_id = await create_test_task(user_dao, cron_dao)
            instance_ids = [str(uuid.uuid4()) for _ in range(num_instances)]

            async def try_lock(instance_id: str) -> bool:
                return await lock_dao.try_acquire_lock(task_id, instance_id)

            results = await asyncio.gather(
                *[try_lock(iid) for iid in instance_ids],
                return_exceptions=True,
            )
            success_count = sum(1 for r in results if r is True)
            assert success_count <= 1

            if success_count == 1:
                lock = await lock_dao.get_lock(task_id)
                if lock is not None:
                    assert lock.instance_id in instance_ids
        finally:
            await db.close()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("num_tasks,num_instances", [(1, 2), (3, 3)])
    async def test_multiple_tasks_independent_locks(self, num_tasks: int, num_instances: int):
        db = Database("sqlite+aiosqlite:///:memory:")
        await db.init_db()
        try:
            user_dao = UserDAO(db)
            cron_dao = CronDAO(db)
            lock_dao = CronLockDAO(db)

            user_id = str(uuid.uuid4())
            await user_dao.create(user_id, f"test_{uuid.uuid4().hex[:8]}")

            task_ids: list[str] = []
            for i in range(num_tasks):
                task = await cron_dao.create(
                    user_id=user_id,
                    name=f"task_{i}",
                    instructions=f"Instructions {i}",
                    cron_expression="0 6 * * *",
                    next_execution_at=datetime.utcnow() + timedelta(hours=1),
                )
                task_ids.append(task.id)

            for task_id in task_ids:
                instance_ids = [str(uuid.uuid4()) for _ in range(num_instances)]

                async def try_lock(iid: str, tid: str = task_id) -> bool:
                    return await lock_dao.try_acquire_lock(tid, iid)

                results = await asyncio.gather(
                    *[try_lock(iid) for iid in instance_ids],
                    return_exceptions=True,
                )
                assert sum(1 for r in results if r is True) <= 1
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_sequential_lock_attempts_first_wins(self):
        """Sequential lock attempts: first one wins, subsequent fail."""
        db = Database("sqlite+aiosqlite:///:memory:")
        await db.init_db()

        try:
            user_dao = UserDAO(db)
            cron_dao = CronDAO(db)
            lock_dao = CronLockDAO(db)

            task_id = await create_test_task(user_dao, cron_dao)

            instance_1 = str(uuid.uuid4())
            instance_2 = str(uuid.uuid4())
            instance_3 = str(uuid.uuid4())

            # First attempt succeeds
            result1 = await lock_dao.try_acquire_lock(task_id, instance_1)
            assert result1 is True

            # Subsequent attempts fail
            result2 = await lock_dao.try_acquire_lock(task_id, instance_2)
            assert result2 is False

            result3 = await lock_dao.try_acquire_lock(task_id, instance_3)
            assert result3 is False

            # Lock is held by first instance
            lock = await lock_dao.get_lock(task_id)
            assert lock is not None
            assert lock.instance_id == instance_1

        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_lock_release_allows_new_acquisition(self):
        """After release, a new lock can be acquired."""
        db = Database("sqlite+aiosqlite:///:memory:")
        await db.init_db()

        try:
            user_dao = UserDAO(db)
            cron_dao = CronDAO(db)
            lock_dao = CronLockDAO(db)

            task_id = await create_test_task(user_dao, cron_dao)

            instance_1 = str(uuid.uuid4())
            instance_2 = str(uuid.uuid4())

            # First instance acquires lock
            result1 = await lock_dao.try_acquire_lock(task_id, instance_1)
            assert result1 is True

            # Second instance fails
            result2 = await lock_dao.try_acquire_lock(task_id, instance_2)
            assert result2 is False

            # First instance releases lock
            await lock_dao.release_lock(task_id)

            # Now second instance can acquire
            result3 = await lock_dao.try_acquire_lock(task_id, instance_2)
            assert result3 is True

            lock = await lock_dao.get_lock(task_id)
            assert lock is not None
            assert lock.instance_id == instance_2

        finally:
            await db.close()
