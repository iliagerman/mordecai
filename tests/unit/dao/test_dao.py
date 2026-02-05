"""Unit tests for DAO layer.

Tests verify:
- DAOs return Pydantic models, not SQLAlchemy objects
- Property-based tests for correctness properties

Requirements: 14.5, 14.6
"""

import uuid

import pytest
import pytest_asyncio

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

    async def test_user_dao_get_or_create_reuses_existing_by_telegram_id(self, user_dao: UserDAO):
        """Ensure get_or_create is safe when telegram_id already exists.

        This simulates a retry/race where a user row exists for a given telegram_id
        but a worker attempts to create it again.
        """
        telegram_id = "279033263"
        existing = await user_dao.create(user_id=str(uuid.uuid4()), telegram_id=telegram_id)

        # Different user_id, same telegram_id should not raise IntegrityError.
        got = await user_dao.get_or_create(user_id="some-other-id", telegram_id=telegram_id)
        assert got.id == existing.id
        assert got.telegram_id == telegram_id

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


class TestLongMemoryRoundTrip:
    """Deterministic tests for long memory persistence round-trip."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "key,value",
        [
            ("favorite_color", "blue"),
            ("timezone", "UTC"),
            ("short", "x"),
        ],
    )
    async def test_memory_round_trip(self, key: str, value: str):
        """Saving a key/value and retrieving returns the same value."""
        db = Database("sqlite+aiosqlite:///:memory:")
        await db.init_db()
        try:
            user_dao = UserDAO(db)
            memory_dao = MemoryDAO(db)

            user_id = str(uuid.uuid4())
            await user_dao.create(user_id, f"test_{uuid.uuid4().hex[:8]}")

            await memory_dao.upsert(user_id, key, value)
            retrieved = await memory_dao.get(user_id, key)

            assert retrieved is not None
            assert retrieved.key == key
            assert retrieved.value == value
            assert retrieved.user_id == user_id
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_memory_update_round_trip(self, memory_dao: MemoryDAO, user_dao: UserDAO):
        """Updating an existing key persists the new value."""
        user_id = str(uuid.uuid4())
        await user_dao.create(user_id, f"test_{uuid.uuid4().hex[:8]}")

        await memory_dao.upsert(user_id, "key1", "value1")
        await memory_dao.upsert(user_id, "key1", "value2")

        result = await memory_dao.get(user_id, "key1")
        assert result is not None
        assert result.value == "value2"


class TestTaskUserIsolation:
    """Deterministic tests for per-user task isolation."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("num_tasks_user1,num_tasks_user2", [(0, 0), (1, 0), (0, 2), (3, 1)])
    async def test_task_user_isolation(self, num_tasks_user1: int, num_tasks_user2: int):
        db = Database("sqlite+aiosqlite:///:memory:")
        await db.init_db()
        try:
            user_dao = UserDAO(db)
            task_dao = TaskDAO(db)

            user1_id = str(uuid.uuid4())
            user2_id = str(uuid.uuid4())
            await user_dao.create(user1_id, f"user1_{uuid.uuid4().hex[:8]}")
            await user_dao.create(user2_id, f"user2_{uuid.uuid4().hex[:8]}")

            for i in range(num_tasks_user1):
                await task_dao.create(str(uuid.uuid4()), user1_id, f"User1 Task {i}")
            for i in range(num_tasks_user2):
                await task_dao.create(str(uuid.uuid4()), user2_id, f"User2 Task {i}")

            user1_tasks = await task_dao.get_by_user(user1_id)
            user2_tasks = await task_dao.get_by_user(user2_id)

            assert len(user1_tasks) == num_tasks_user1
            assert len(user2_tasks) == num_tasks_user2
            assert all(t.user_id == user1_id for t in user1_tasks)
            assert all(t.user_id == user2_id for t in user2_tasks)
            assert {t.id for t in user1_tasks}.isdisjoint({t.id for t in user2_tasks})
        finally:
            await db.close()


class TestTaskStatusDatabaseUpdate:
    """Deterministic tests for updating task status in the DB."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "initial_status,new_status",
        [
            (TaskStatus.PENDING, TaskStatus.IN_PROGRESS),
            (TaskStatus.PENDING, TaskStatus.DONE),
            (TaskStatus.IN_PROGRESS, TaskStatus.DONE),
            (TaskStatus.DONE, TaskStatus.DONE),
        ],
    )
    async def test_task_status_update(self, initial_status: TaskStatus, new_status: TaskStatus):
        db = Database("sqlite+aiosqlite:///:memory:")
        await db.init_db()
        try:
            user_dao = UserDAO(db)
            task_dao = TaskDAO(db)

            user_id = str(uuid.uuid4())
            await user_dao.create(user_id, f"test_{uuid.uuid4().hex[:8]}")

            task_id = str(uuid.uuid4())
            await task_dao.create(task_id, user_id, "Test Task")

            if initial_status != TaskStatus.PENDING:
                assert await task_dao.update_status(task_id, initial_status) is True

            assert await task_dao.update_status(task_id, new_status) is True
            retrieved = await task_dao.get_by_id(task_id)
            assert retrieved is not None
            assert retrieved.status == new_status
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_task_status_update_nonexistent_task(self, task_dao: TaskDAO):
        """Updating non-existent task returns False."""
        assert await task_dao.update_status(str(uuid.uuid4()), TaskStatus.DONE) is False
