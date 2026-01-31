"""Unit tests for Cron DAO layer.

Tests verify:
- CronDAO returns Pydantic models, not SQLAlchemy objects
- Property-based tests for correctness properties

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6
"""

import uuid
from datetime import datetime, timedelta

import pytest
import pytest_asyncio

from app.dao.cron_dao import CronDAO
from app.dao.user_dao import UserDAO
from app.database import Database
from app.models.domain import CronTask


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


class TestCronDAOReturnsPydanticModels:
    """Tests verifying CronDAO returns Pydantic models, not SQLAlchemy objects.

    **Property 7: DAO Returns Domain Models**
    *For any* DAO method that returns cron task data, the return type should
    be a Pydantic CronTask domain model, never a SQLAlchemy ORM object.
    **Validates: Requirements 3.6**
    """

    async def test_cron_dao_create_returns_pydantic(self, cron_dao: CronDAO, user_dao: UserDAO):
        """Verify CronDAO.create returns Pydantic CronTask model."""
        user_id = str(uuid.uuid4())
        await user_dao.create(user_id, f"test_{uuid.uuid4().hex[:8]}")

        next_exec = datetime.utcnow() + timedelta(hours=1)
        result = await cron_dao.create(
            user_id=user_id,
            name="test_task",
            instructions="Do something",
            cron_expression="0 6 * * *",
            next_execution_at=next_exec,
        )

        assert isinstance(result, CronTask)
        assert result.user_id == user_id
        assert result.name == "test_task"
        assert result.instructions == "Do something"
        assert result.cron_expression == "0 6 * * *"

    async def test_cron_dao_get_by_id_returns_pydantic(self, cron_dao: CronDAO, user_dao: UserDAO):
        """Verify CronDAO.get_by_id returns Pydantic CronTask model."""
        user_id = str(uuid.uuid4())
        await user_dao.create(user_id, f"test_{uuid.uuid4().hex[:8]}")

        next_exec = datetime.utcnow() + timedelta(hours=1)
        created = await cron_dao.create(
            user_id=user_id,
            name="test_task",
            instructions="Do something",
            cron_expression="0 6 * * *",
            next_execution_at=next_exec,
        )

        result = await cron_dao.get_by_id(created.id)

        assert isinstance(result, CronTask)
        assert result.id == created.id

    async def test_cron_dao_get_by_user_and_name_returns_pydantic(
        self, cron_dao: CronDAO, user_dao: UserDAO
    ):
        """Verify CronDAO.get_by_user_and_name returns Pydantic CronTask model."""
        user_id = str(uuid.uuid4())
        await user_dao.create(user_id, f"test_{uuid.uuid4().hex[:8]}")

        next_exec = datetime.utcnow() + timedelta(hours=1)
        await cron_dao.create(
            user_id=user_id,
            name="unique_task",
            instructions="Do something",
            cron_expression="0 6 * * *",
            next_execution_at=next_exec,
        )

        result = await cron_dao.get_by_user_and_name(user_id, "unique_task")

        assert isinstance(result, CronTask)
        assert result.name == "unique_task"

    async def test_cron_dao_list_by_user_returns_pydantic_list(
        self, cron_dao: CronDAO, user_dao: UserDAO
    ):
        """Verify CronDAO.list_by_user returns list of Pydantic CronTask models."""
        user_id = str(uuid.uuid4())
        await user_dao.create(user_id, f"test_{uuid.uuid4().hex[:8]}")

        next_exec = datetime.utcnow() + timedelta(hours=1)
        await cron_dao.create(
            user_id=user_id,
            name="task1",
            instructions="Do something 1",
            cron_expression="0 6 * * *",
            next_execution_at=next_exec,
        )
        await cron_dao.create(
            user_id=user_id,
            name="task2",
            instructions="Do something 2",
            cron_expression="0 7 * * *",
            next_execution_at=next_exec,
        )

        result = await cron_dao.list_by_user(user_id)

        assert isinstance(result, list)
        assert all(isinstance(t, CronTask) for t in result)
        assert len(result) == 2

    async def test_cron_dao_get_due_tasks_returns_pydantic_list(
        self, cron_dao: CronDAO, user_dao: UserDAO
    ):
        """Verify CronDAO.get_due_tasks returns list of Pydantic CronTask models."""
        user_id = str(uuid.uuid4())
        await user_dao.create(user_id, f"test_{uuid.uuid4().hex[:8]}")

        # Create a task that is due (next_execution_at in the past)
        past_time = datetime.utcnow() - timedelta(hours=1)
        await cron_dao.create(
            user_id=user_id,
            name="due_task",
            instructions="Do something",
            cron_expression="0 6 * * *",
            next_execution_at=past_time,
        )

        result = await cron_dao.get_due_tasks(datetime.utcnow())

        assert isinstance(result, list)
        assert all(isinstance(t, CronTask) for t in result)
        assert len(result) == 1


class TestDueTasksQueryCorrectness:
    """Deterministic tests for get_due_tasks correctness."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "num_due_enabled,num_due_disabled,num_future_enabled",
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (2, 0, 2)],
    )
    async def test_due_tasks_query_correctness(
        self,
        num_due_enabled: int,
        num_due_disabled: int,
        num_future_enabled: int,
    ):
        db = Database("sqlite+aiosqlite:///:memory:")
        await db.init_db()
        try:
            user_dao = UserDAO(db)
            cron_dao = CronDAO(db)

            user_id = str(uuid.uuid4())
            await user_dao.create(user_id, f"test_{uuid.uuid4().hex[:8]}")

            now = datetime.utcnow()
            past_time = now - timedelta(hours=1)
            future_time = now + timedelta(hours=1)

            expected_due_ids: set[str] = set()

            for i in range(num_due_enabled):
                task = await cron_dao.create(
                    user_id=user_id,
                    name=f"due_enabled_{i}",
                    instructions=f"Task {i}",
                    cron_expression="0 6 * * *",
                    next_execution_at=past_time,
                )
                expected_due_ids.add(task.id)

            for i in range(num_due_disabled):
                task = await cron_dao.create(
                    user_id=user_id,
                    name=f"due_disabled_{i}",
                    instructions=f"Task {i}",
                    cron_expression="0 6 * * *",
                    next_execution_at=past_time,
                )
                async with db.session() as session:
                    from sqlalchemy import select

                    from app.models.orm import CronTaskModel

                    result = await session.execute(
                        select(CronTaskModel).where(CronTaskModel.id == task.id)
                    )
                    model = result.scalar_one()
                    model.enabled = False

            for i in range(num_future_enabled):
                await cron_dao.create(
                    user_id=user_id,
                    name=f"future_enabled_{i}",
                    instructions=f"Task {i}",
                    cron_expression="0 6 * * *",
                    next_execution_at=future_time,
                )

            due_tasks = await cron_dao.get_due_tasks(now)
            assert {t.id for t in due_tasks} == expected_due_ids

            for task in due_tasks:
                assert task.enabled is True
                assert task.next_execution_at <= now
        finally:
            await db.close()
