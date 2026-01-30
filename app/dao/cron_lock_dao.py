"""Cron lock data access operations for distributed locking."""

from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.dao.base import BaseDAO
from app.models.domain import CronLock
from app.models.orm import CronLockModel


class CronLockDAO(BaseDAO[CronLock]):
    """Data access object for Cron Lock operations.

    Provides distributed locking for cron task execution to prevent
    duplicate execution across multiple instances.

    All methods return Pydantic CronLock models, never SQLAlchemy objects.
    """

    LOCK_TIMEOUT_MINUTES = 10

    async def try_acquire_lock(
        self,
        task_id: str,
        instance_id: str,
    ) -> bool:
        """Attempt to acquire a lock atomically.

        If no lock exists, creates one. If an expired lock exists (>10 min),
        replaces it. If a valid lock exists, returns False.

        Uses database-level atomicity via unique constraint to prevent
        race conditions in concurrent lock acquisition.

        Args:
            task_id: The cron task ID to lock.
            instance_id: Unique identifier for this scheduler instance.

        Returns:
            True if lock was acquired, False if already locked by another.
        """
        now = datetime.utcnow()
        expiry_threshold = now - timedelta(minutes=self.LOCK_TIMEOUT_MINUTES)

        try:
            async with self._db.session() as session:
                # Check for existing lock
                result = await session.execute(
                    select(CronLockModel).where(
                        CronLockModel.task_id == task_id
                    )
                )
                existing_lock = result.scalar_one_or_none()

                if existing_lock is None:
                    # No lock exists, try to create one
                    lock_model = CronLockModel(
                        task_id=task_id,
                        instance_id=instance_id,
                        lock_acquired_at=now,
                    )
                    session.add(lock_model)
                    # Commit happens on context exit
                    return True

                # Lock exists - check if expired (older than 10 minutes)
                if existing_lock.lock_acquired_at < expiry_threshold:
                    # Lock is expired, replace it
                    existing_lock.instance_id = instance_id
                    existing_lock.lock_acquired_at = now
                    return True

                # Lock is still valid and held by another instance
                return False
        except IntegrityError:
            # Another instance acquired the lock concurrently
            return False

    async def release_lock(self, task_id: str) -> bool:
        """Release a lock for a task.

        Args:
            task_id: The cron task ID to unlock.

        Returns:
            True if lock was found and released, False if no lock existed.
        """
        async with self._db.session() as session:
            result = await session.execute(
                delete(CronLockModel).where(CronLockModel.task_id == task_id)
            )
            return result.rowcount > 0

    async def is_locked(self, task_id: str) -> bool:
        """Check if a valid (non-expired) lock exists for a task.

        Args:
            task_id: The cron task ID to check.

        Returns:
            True if a valid lock exists, False otherwise.
        """
        now = datetime.utcnow()
        expiry_threshold = now - timedelta(minutes=self.LOCK_TIMEOUT_MINUTES)

        async with self._db.session() as session:
            result = await session.execute(
                select(CronLockModel).where(CronLockModel.task_id == task_id)
            )
            lock = result.scalar_one_or_none()

            if lock is None:
                return False

            # Check if lock is expired
            return lock.lock_acquired_at >= expiry_threshold

    async def get_lock(self, task_id: str) -> CronLock | None:
        """Get lock information for a task.

        Args:
            task_id: The cron task ID to get lock for.

        Returns:
            CronLock domain model if lock exists, None otherwise.
        """
        async with self._db.session() as session:
            result = await session.execute(
                select(CronLockModel).where(CronLockModel.task_id == task_id)
            )
            lock_model = result.scalar_one_or_none()

            if lock_model is None:
                return None

            return CronLock(
                task_id=lock_model.task_id,
                instance_id=lock_model.instance_id,
                lock_acquired_at=lock_model.lock_acquired_at,
            )
