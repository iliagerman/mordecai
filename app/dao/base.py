"""Base DAO abstract class."""

from abc import ABC
from typing import Generic, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Database

# Type variable for Pydantic domain models
T = TypeVar("T")


class BaseDAO(ABC, Generic[T]):
    """Abstract base class for Data Access Objects.

    DAOs handle all database operations and MUST return Pydantic domain models,
    never SQLAlchemy ORM objects. This ensures clean separation between the
    data layer and business logic.

    All operations are async and use the Database session context manager
    for automatic transaction handling.
    """

    def __init__(self, database: Database):
        """Initialize DAO with database connection.

        Args:
            database: Database instance for session management.
        """
        self._db = database

    @property
    def db(self) -> Database:
        """Get the database instance."""
        return self._db

    async def _get_session(self) -> AsyncSession:
        """Get a new async session.

        Note: Prefer using `async with self._db.session() as session:`
        for automatic transaction handling.
        """
        return self._db._async_session()
