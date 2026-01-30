"""Async SQLAlchemy database setup."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Database:
    """Async database connection manager.

    Provides async session management with automatic transaction handling.
    All database operations should use the session() context manager.
    """

    def __init__(self, database_url: str):
        """Initialize database with connection URL.

        Args:
            database_url: SQLAlchemy database URL. If using sqlite:///, it will
                         be automatically converted to sqlite+aiosqlite:///.
        """
        # Convert sqlite:/// to sqlite+aiosqlite:/// for async support
        is_sqlite = database_url.startswith("sqlite:///")
        needs_async = "aiosqlite" not in database_url
        if is_sqlite and needs_async:
            database_url = database_url.replace(
                "sqlite:///", "sqlite+aiosqlite:///"
            )

        # Configure engine with SQLite-specific settings for better concurrency
        connect_args = {}
        if is_sqlite:
            # Increase timeout to reduce "database is locked" errors
            connect_args["timeout"] = 30

        self._engine: AsyncEngine = create_async_engine(
            database_url,
            echo=False,
            future=True,
            connect_args=connect_args,
        )
        self._async_session: async_sessionmaker[AsyncSession] = (
            async_sessionmaker(
                self._engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
        )

    @property
    def engine(self) -> AsyncEngine:
        """Get the async engine instance."""
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Provide a transactional scope around a series of operations.

        Usage:
            async with db.session() as session:
                # perform database operations
                session.add(model)
                # commit happens automatically on success
                # rollback happens automatically on exception

        Yields:
            AsyncSession: An async SQLAlchemy session.
        """
        async with self._async_session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def init_db(self) -> None:
        """Initialize database tables from ORM models.

        Creates all tables defined in Base.metadata if they don't exist.
        For production, use Alembic migrations instead.
        """
        async with self._engine.begin() as conn:
            # Enable WAL mode for SQLite to improve concurrent access
            # WAL allows readers and writers to operate concurrently
            dialect_name = conn.dialect.name
            if dialect_name == "sqlite":
                await conn.execute(
                    __import__("sqlalchemy").text("PRAGMA journal_mode=WAL")
                )
                await conn.execute(
                    __import__("sqlalchemy").text("PRAGMA busy_timeout=30000")
                )
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        """Close database connections and dispose of the engine."""
        await self._engine.dispose()
