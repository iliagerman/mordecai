"""Unit tests for DAO layer.

Tests verify:
- DAOs return Pydantic models, not SQLAlchemy objects
- Property-based tests for correctness properties

Requirements: 14.5, 14.6
"""

import uuid

import pytest
import pytest_asyncio
from hypothesis import given, settings, strategies as st

from app.dao.log_dao import LogDAO
from app.dao.memory_dao import MemoryDAO
from app.dao.task_dao import TaskDAO
from app.dao.user_dao import UserDAO
from app.database import Database
from app.enums import LogSeverity, TaskStatus
from app.models.domain import LogEntry, LongMemory, Task, User


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
async def task_dao(test_db: Database) -> TaskDAO:
    """Create TaskDAO instance."""
    return TaskDAO(test_db)


@pytest_asyncio.fixture
async def log_dao(test_db: Database) -> LogDAO:
    """Create LogDAO instance."""
    return LogDAO(test_db)


@pytest_asyncio.fixture
async def memory_dao(test_db: Database) -> MemoryDAO:
    """Create MemoryDAO instance."""
    return MemoryDAO(test_db)


class TestDAOReturnsPydanticModels:
    """Tests verifying DAOs return Pydantic models, not SQLAlchemy objects."""

    async def test_user_dao_returns_pydantic_user(self, user_dao: UserDAO):
        """Verify UserDAO.create returns Pydantic User model."""
        user_id = str(uuid.uuid4())
        telegram_id = f"test_{uuid.uuid4().hex[:8]}"

        result = await user_dao.create(user_id, telegram_id)

        assert isinstance(result, User)
        assert result.id == user_id
        assert result.telegram_id == telegram_id

    async def test_user_dao_get_by_id_returns_pydantic(self, user_dao: UserDAO):
        """Verify UserDAO.get_by_id returns Pydantic User model."""
        user_id = str(uuid.uuid4())
        telegram_id = f"test_{uuid.uuid4().hex[:8]}"
        await user_dao.create(user_id, telegram_id)

        result = await user_dao.get_by_id(user_id)

        assert isinstance(result, User)

    async def test_task_dao_returns_pydantic_task(self, task_dao: TaskDAO, user_dao: UserDAO):
        """Verify TaskDAO.create returns Pydantic Task model."""
        user_id = str(uuid.uuid4())
        await user_dao.create(user_id, f"test_{uuid.uuid4().hex[:8]}")

        task_id = str(uuid.uuid4())
        result = await task_dao.create(task_id, user_id, "Test Task")

        assert isinstance(result, Task)
        assert result.id == task_id
        assert result.user_id == user_id

    async def test_task_dao_get_by_user_returns_pydantic_list(
        self, task_dao: TaskDAO, user_dao: UserDAO
    ):
        """Verify TaskDAO.get_by_user returns list of Pydantic Task models."""
        user_id = str(uuid.uuid4())
        await user_dao.create(user_id, f"test_{uuid.uuid4().hex[:8]}")
        await task_dao.create(str(uuid.uuid4()), user_id, "Task 1")
        await task_dao.create(str(uuid.uuid4()), user_id, "Task 2")

        result = await task_dao.get_by_user(user_id)

        assert isinstance(result, list)
        assert all(isinstance(t, Task) for t in result)

    async def test_memory_dao_returns_pydantic_memory(
        self, memory_dao: MemoryDAO, user_dao: UserDAO
    ):
        """Verify MemoryDAO.upsert returns Pydantic LongMemory model."""
        user_id = str(uuid.uuid4())
        await user_dao.create(user_id, f"test_{uuid.uuid4().hex[:8]}")

        result = await memory_dao.upsert(user_id, "test_key", "test_value")

        assert isinstance(result, LongMemory)
        assert result.user_id == user_id
        assert result.key == "test_key"
        assert result.value == "test_value"

    async def test_log_dao_returns_pydantic_log_entry(self, log_dao: LogDAO, user_dao: UserDAO):
        """Verify LogDAO.create returns Pydantic LogEntry model."""
        user_id = str(uuid.uuid4())
        await user_dao.create(user_id, f"test_{uuid.uuid4().hex[:8]}")

        result = await log_dao.create(
            user_id=user_id,
            action="Test action",
            severity=LogSeverity.INFO,
            details={"key": "value"},
        )

        assert isinstance(result, LogEntry)
        assert result.user_id == user_id
        assert result.action == "Test action"
        assert result.severity == LogSeverity.INFO
        assert result.details == {"key": "value"}

    async def test_log_dao_get_recent_returns_pydantic_list(
        self, log_dao: LogDAO, user_dao: UserDAO
    ):
        """Verify LogDAO.get_recent returns list of Pydantic LogEntry models."""
        user_id = str(uuid.uuid4())
        await user_dao.create(user_id, f"test_{uuid.uuid4().hex[:8]}")
        await log_dao.create(user_id, "Action 1")
        await log_dao.create(user_id, "Action 2")

        result = await log_dao.get_recent(user_id)

        assert isinstance(result, list)
        assert all(isinstance(log, LogEntry) for log in result)


class TestProperty10LongMemoryRoundTrip:
    """Property 10: Long Memory Persistence Round-Trip.

    *For any* information saved to long-term memory, querying should return
    the same information.

    **Validates: Requirements 4.2, 4.3, 4.4**
    """

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        key=st.text(min_size=1, max_size=50).filter(lambda x: x.strip()),
        value=st.text(min_size=1, max_size=500).filter(lambda x: x.strip()),
    )
    async def test_memory_round_trip(self, key: str, value: str):
        """Feature: mordecai, Property 10: Long Memory Round-Trip.

        For any key-value pair, saving to memory and then retrieving should
        return the exact same value.
        """
        # Setup fresh database for each test
        db = Database("sqlite+aiosqlite:///:memory:")
        await db.init_db()

        try:
            user_dao = UserDAO(db)
            memory_dao = MemoryDAO(db)

            # Create user
            user_id = str(uuid.uuid4())
            await user_dao.create(user_id, f"test_{uuid.uuid4().hex[:8]}")

            # Save memory
            await memory_dao.upsert(user_id, key, value)

            # Retrieve memory
            retrieved = await memory_dao.get(user_id, key)

            # Verify round-trip
            assert retrieved is not None
            assert retrieved.key == key
            assert retrieved.value == value
            assert retrieved.user_id == user_id
        finally:
            await db.close()

    async def test_memory_update_round_trip(self, memory_dao: MemoryDAO, user_dao: UserDAO):
        """Verify updating memory preserves the new value."""
        user_id = str(uuid.uuid4())
        await user_dao.create(user_id, f"test_{uuid.uuid4().hex[:8]}")

        # Initial save
        await memory_dao.upsert(user_id, "key1", "value1")

        # Update
        await memory_dao.upsert(user_id, "key1", "value2")

        # Retrieve
        result = await memory_dao.get(user_id, "key1")

        assert result is not None
        assert result.value == "value2"


class TestProperty21TaskUserIsolation:
    """Property 21: Task User Isolation.

    *For any* user, querying tasks should return only tasks belonging to that user.

    **Validates: Requirements 8.7**
    """

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        num_tasks_user1=st.integers(min_value=0, max_value=5),
        num_tasks_user2=st.integers(min_value=0, max_value=5),
    )
    async def test_task_user_isolation(self, num_tasks_user1: int, num_tasks_user2: int):
        """Feature: mordecai, Property 21: Task User Isolation.

        For any two users with any number of tasks, querying tasks for one user
        should only return that user's tasks.
        """
        db = Database("sqlite+aiosqlite:///:memory:")
        await db.init_db()

        try:
            user_dao = UserDAO(db)
            task_dao = TaskDAO(db)

            # Create two users
            user1_id = str(uuid.uuid4())
            user2_id = str(uuid.uuid4())
            await user_dao.create(user1_id, f"user1_{uuid.uuid4().hex[:8]}")
            await user_dao.create(user2_id, f"user2_{uuid.uuid4().hex[:8]}")

            # Create tasks for user1
            user1_task_ids = []
            for i in range(num_tasks_user1):
                task_id = str(uuid.uuid4())
                user1_task_ids.append(task_id)
                await task_dao.create(task_id, user1_id, f"User1 Task {i}")

            # Create tasks for user2
            user2_task_ids = []
            for i in range(num_tasks_user2):
                task_id = str(uuid.uuid4())
                user2_task_ids.append(task_id)
                await task_dao.create(task_id, user2_id, f"User2 Task {i}")

            # Query tasks for user1
            user1_tasks = await task_dao.get_by_user(user1_id)

            # Query tasks for user2
            user2_tasks = await task_dao.get_by_user(user2_id)

            # Verify isolation
            assert len(user1_tasks) == num_tasks_user1
            assert len(user2_tasks) == num_tasks_user2

            # All user1 tasks belong to user1
            assert all(t.user_id == user1_id for t in user1_tasks)
            # All user2 tasks belong to user2
            assert all(t.user_id == user2_id for t in user2_tasks)

            # No overlap in task IDs
            user1_retrieved_ids = {t.id for t in user1_tasks}
            user2_retrieved_ids = {t.id for t in user2_tasks}
            assert user1_retrieved_ids.isdisjoint(user2_retrieved_ids)
        finally:
            await db.close()


class TestProperty28TaskStatusDatabaseUpdate:
    """Property 28: Task Status Database Update.

    *For any* task status change through the API, the database should reflect
    the updated status.

    **Validates: Requirements 13.5**
    """

    @pytest.mark.asyncio
    @settings(max_examples=100)
    @given(
        initial_status=st.sampled_from(list(TaskStatus)),
        new_status=st.sampled_from(list(TaskStatus)),
    )
    async def test_task_status_update(self, initial_status: TaskStatus, new_status: TaskStatus):
        """Feature: mordecai, Property 28: Task Status Database Update.

        For any task with any initial status, updating to any new status should
        be reflected when querying the database.
        """
        db = Database("sqlite+aiosqlite:///:memory:")
        await db.init_db()

        try:
            user_dao = UserDAO(db)
            task_dao = TaskDAO(db)

            # Create user
            user_id = str(uuid.uuid4())
            await user_dao.create(user_id, f"test_{uuid.uuid4().hex[:8]}")

            # Create task with initial status
            task_id = str(uuid.uuid4())
            await task_dao.create(task_id, user_id, "Test Task")

            # If initial status is not PENDING, update it first
            if initial_status != TaskStatus.PENDING:
                await task_dao.update_status(task_id, initial_status)

            # Update to new status
            success = await task_dao.update_status(task_id, new_status)

            # Verify update succeeded
            assert success is True

            # Retrieve and verify
            retrieved = await task_dao.get_by_id(task_id)
            assert retrieved is not None
            assert retrieved.status == new_status
        finally:
            await db.close()

    async def test_task_status_update_nonexistent_task(self, task_dao: TaskDAO):
        """Verify updating non-existent task returns False."""
        result = await task_dao.update_status(str(uuid.uuid4()), TaskStatus.DONE)
        assert result is False
