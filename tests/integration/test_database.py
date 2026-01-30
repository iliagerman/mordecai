"""Integration tests for database setup.

Tests verify:
- Database initialization creates all tables
- Async session management works correctly
- Basic CRUD operations with ORM models
- Relationships between models work correctly

Requirements: 14.5
"""

import uuid
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import inspect, select, text

from app.database import Database
from app.enums import LogSeverity, TaskStatus
from app.models.orm import (
    LogModel,
    SkillMetadataModel,
    TaskModel,
    UserModel,
)


@pytest_asyncio.fixture
async def test_db():
    """Create a fresh in-memory database for each test."""
    db = Database("sqlite+aiosqlite:///:memory:")
    await db.init_db()
    yield db
    await db.close()


class TestDatabaseInitialization:
    """Tests for database initialization."""

    async def test_init_db_creates_all_tables(self, test_db: Database):
        """Verify init_db creates all expected tables."""
        async with test_db.engine.connect() as conn:
            # Get table names using sync inspection
            table_names = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )

        expected_tables = {
            "users",
            "tasks",
            "logs",
            "skills",
            "cron_tasks",
            "cron_locks",
            "long_memory",
        }
        assert expected_tables == set(table_names)

    async def test_database_url_conversion_sqlite(self):
        """Verify sqlite:/// URLs are converted to async format."""
        db = Database("sqlite:///./test.db")
        # The engine URL should contain aiosqlite
        assert "aiosqlite" in str(db.engine.url)
        await db.close()

    async def test_database_url_preserves_async_format(self):
        """Verify already-async URLs are not modified."""
        db = Database("sqlite+aiosqlite:///:memory:")
        assert "aiosqlite" in str(db.engine.url)
        await db.close()


class TestAsyncSessionManagement:
    """Tests for async session context manager."""

    async def test_session_commits_on_success(self, test_db: Database):
        """Verify session commits automatically on successful operations."""
        user_id = str(uuid.uuid4())

        async with test_db.session() as session:
            user = UserModel(
                id=user_id,
                telegram_id="123456789",
                created_at=datetime.utcnow(),
                last_active=datetime.utcnow(),
            )
            session.add(user)

        # Verify data persisted in a new session
        async with test_db.session() as session:
            result = await session.execute(
                select(UserModel).where(UserModel.id == user_id)
            )
            fetched_user = result.scalar_one_or_none()
            assert fetched_user is not None
            assert fetched_user.telegram_id == "123456789"

    async def test_session_rollbacks_on_exception(self, test_db: Database):
        """Verify session rolls back on exception."""
        user_id = str(uuid.uuid4())

        with pytest.raises(ValueError):
            async with test_db.session() as session:
                user = UserModel(
                    id=user_id,
                    telegram_id="rollback_test",
                    created_at=datetime.utcnow(),
                    last_active=datetime.utcnow(),
                )
                session.add(user)
                raise ValueError("Simulated error")

        # Verify data was not persisted
        async with test_db.session() as session:
            result = await session.execute(
                select(UserModel).where(UserModel.id == user_id)
            )
            fetched_user = result.scalar_one_or_none()
            assert fetched_user is None


class TestUserModelCRUD:
    """Tests for User model CRUD operations."""

    async def test_create_user(self, test_db: Database):
        """Verify user creation works."""
        user_id = str(uuid.uuid4())
        telegram_id = "test_telegram_123"

        async with test_db.session() as session:
            user = UserModel(
                id=user_id,
                telegram_id=telegram_id,
                created_at=datetime.utcnow(),
                last_active=datetime.utcnow(),
            )
            session.add(user)

        async with test_db.session() as session:
            result = await session.execute(
                select(UserModel).where(UserModel.id == user_id)
            )
            user = result.scalar_one()
            assert user.telegram_id == telegram_id

    async def test_user_telegram_id_unique_constraint(self, test_db: Database):
        """Verify telegram_id uniqueness is enforced."""
        telegram_id = "duplicate_telegram"

        async with test_db.session() as session:
            user1 = UserModel(
                id=str(uuid.uuid4()),
                telegram_id=telegram_id,
                created_at=datetime.utcnow(),
                last_active=datetime.utcnow(),
            )
            session.add(user1)

        with pytest.raises(Exception):  # IntegrityError
            async with test_db.session() as session:
                user2 = UserModel(
                    id=str(uuid.uuid4()),
                    telegram_id=telegram_id,
                    created_at=datetime.utcnow(),
                    last_active=datetime.utcnow(),
                )
                session.add(user2)


class TestTaskModelCRUD:
    """Tests for Task model CRUD operations."""

    @pytest_asyncio.fixture
    async def test_user(self, test_db: Database) -> str:
        """Create a test user and return their ID."""
        user_id = str(uuid.uuid4())
        async with test_db.session() as session:
            user = UserModel(
                id=user_id,
                telegram_id=f"task_test_{uuid.uuid4().hex[:8]}",
                created_at=datetime.utcnow(),
                last_active=datetime.utcnow(),
            )
            session.add(user)
        return user_id

    async def test_create_task(self, test_db: Database, test_user: str):
        """Verify task creation works."""
        task_id = str(uuid.uuid4())

        async with test_db.session() as session:
            task = TaskModel(
                id=task_id,
                user_id=test_user,
                title="Test Task",
                description="Test description",
                status=TaskStatus.PENDING.value,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            session.add(task)

        async with test_db.session() as session:
            result = await session.execute(
                select(TaskModel).where(TaskModel.id == task_id)
            )
            task = result.scalar_one()
            assert task.title == "Test Task"
            assert task.status == TaskStatus.PENDING.value

    async def test_task_status_update(self, test_db: Database, test_user: str):
        """Verify task status can be updated."""
        task_id = str(uuid.uuid4())

        async with test_db.session() as session:
            task = TaskModel(
                id=task_id,
                user_id=test_user,
                title="Status Test",
                status=TaskStatus.PENDING.value,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            session.add(task)

        async with test_db.session() as session:
            result = await session.execute(
                select(TaskModel).where(TaskModel.id == task_id)
            )
            task = result.scalar_one()
            task.status = TaskStatus.IN_PROGRESS.value

        async with test_db.session() as session:
            result = await session.execute(
                select(TaskModel).where(TaskModel.id == task_id)
            )
            task = result.scalar_one()
            assert task.status == TaskStatus.IN_PROGRESS.value


class TestLogModelCRUD:
    """Tests for Log model CRUD operations."""

    @pytest_asyncio.fixture
    async def test_user(self, test_db: Database) -> str:
        """Create a test user and return their ID."""
        user_id = str(uuid.uuid4())
        async with test_db.session() as session:
            user = UserModel(
                id=user_id,
                telegram_id=f"log_test_{uuid.uuid4().hex[:8]}",
                created_at=datetime.utcnow(),
                last_active=datetime.utcnow(),
            )
            session.add(user)
        return user_id

    async def test_create_log(self, test_db: Database, test_user: str):
        """Verify log creation works."""
        async with test_db.session() as session:
            log = LogModel(
                user_id=test_user,
                action="Test action",
                severity=LogSeverity.INFO.value,
                details='{"key": "value"}',
                timestamp=datetime.utcnow(),
            )
            session.add(log)

        async with test_db.session() as session:
            result = await session.execute(
                select(LogModel).where(LogModel.user_id == test_user)
            )
            log = result.scalar_one()
            assert log.action == "Test action"
            assert log.severity == LogSeverity.INFO.value


class TestSkillMetadataModelCRUD:
    """Tests for SkillMetadata model CRUD operations."""

    async def test_create_skill(self, test_db: Database):
        """Verify skill metadata creation works."""
        async with test_db.session() as session:
            skill = SkillMetadataModel(
                name="test_skill",
                source_url="https://example.com/skill.zip",
                installed_at=datetime.utcnow(),
                version="1.0.0",
            )
            session.add(skill)

        async with test_db.session() as session:
            result = await session.execute(
                select(SkillMetadataModel).where(
                    SkillMetadataModel.name == "test_skill"
                )
            )
            skill = result.scalar_one()
            assert skill.source_url == "https://example.com/skill.zip"
            assert skill.version == "1.0.0"


class TestModelRelationships:
    """Tests for ORM model relationships."""

    @pytest_asyncio.fixture
    async def test_user(self, test_db: Database) -> str:
        """Create a test user and return their ID."""
        user_id = str(uuid.uuid4())
        async with test_db.session() as session:
            user = UserModel(
                id=user_id,
                telegram_id=f"rel_test_{uuid.uuid4().hex[:8]}",
                created_at=datetime.utcnow(),
                last_active=datetime.utcnow(),
            )
            session.add(user)
        return user_id

    async def test_user_tasks_relationship(self, test_db: Database, test_user: str):
        """Verify user-tasks relationship works."""
        task_id = str(uuid.uuid4())

        async with test_db.session() as session:
            task = TaskModel(
                id=task_id,
                user_id=test_user,
                title="Relationship Test",
                status=TaskStatus.PENDING.value,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            session.add(task)

        async with test_db.session() as session:
            result = await session.execute(
                select(TaskModel).where(TaskModel.user_id == test_user)
            )
            tasks = result.scalars().all()
            assert len(tasks) == 1
            assert tasks[0].title == "Relationship Test"

    async def test_cascade_delete_user_tasks(self, test_db: Database):
        """Verify deleting user cascades to tasks."""
        user_id = str(uuid.uuid4())
        task_id = str(uuid.uuid4())

        # Create user with task
        async with test_db.session() as session:
            user = UserModel(
                id=user_id,
                telegram_id=f"cascade_test_{uuid.uuid4().hex[:8]}",
                created_at=datetime.utcnow(),
                last_active=datetime.utcnow(),
            )
            session.add(user)

        async with test_db.session() as session:
            task = TaskModel(
                id=task_id,
                user_id=user_id,
                title="Cascade Test",
                status=TaskStatus.PENDING.value,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            session.add(task)

        # Delete user
        async with test_db.session() as session:
            result = await session.execute(
                select(UserModel).where(UserModel.id == user_id)
            )
            user = result.scalar_one()
            await session.delete(user)

        # Verify task was also deleted
        async with test_db.session() as session:
            result = await session.execute(
                select(TaskModel).where(TaskModel.id == task_id)
            )
            task = result.scalar_one_or_none()
            assert task is None
