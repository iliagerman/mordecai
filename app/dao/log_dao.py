"""Log data access operations."""

import json
from datetime import datetime, timedelta

from sqlalchemy import select

from app.dao.base import BaseDAO
from app.enums import LogSeverity
from app.models.domain import LogEntry
from app.models.orm import LogModel


class LogDAO(BaseDAO[LogEntry]):
    """Data access object for Log operations.

    All methods return Pydantic LogEntry models, never SQLAlchemy objects.
    """

    async def create(
        self,
        user_id: str,
        action: str,
        severity: LogSeverity = LogSeverity.INFO,
        details: dict | None = None,
    ) -> LogEntry:
        """Create a new log entry.

        Args:
            user_id: User identifier.
            action: Description of the action being logged.
            severity: Log severity level.
            details: Optional additional details as a dict.

        Returns:
            Created LogEntry domain model.
        """
        now = datetime.utcnow()
        async with self._db.session() as session:
            log_model = LogModel(
                user_id=user_id,
                action=action,
                severity=severity.value,
                details=json.dumps(details) if details else None,
                timestamp=now,
            )
            session.add(log_model)
            await session.flush()

            return LogEntry(
                id=log_model.id,
                user_id=log_model.user_id,
                action=log_model.action,
                severity=LogSeverity(log_model.severity),
                details=details,
                timestamp=log_model.timestamp,
            )

    async def get_recent(
        self,
        user_id: str,
        hours: int = 24,
        severity: LogSeverity | None = None,
    ) -> list[LogEntry]:
        """Get recent log entries for a user.

        Args:
            user_id: User identifier.
            hours: Number of hours to look back (default 24).
            severity: Optional severity filter.

        Returns:
            List of LogEntry domain models.
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours)

        async with self._db.session() as session:
            query = (
                select(LogModel)
                .where(LogModel.user_id == user_id)
                .where(LogModel.timestamp >= cutoff)
            )

            if severity is not None:
                query = query.where(LogModel.severity == severity.value)

            query = query.order_by(LogModel.timestamp.desc())

            result = await session.execute(query)
            log_models = result.scalars().all()

            return [
                LogEntry(
                    id=log.id,
                    user_id=log.user_id,
                    action=log.action,
                    severity=LogSeverity(log.severity),
                    details=json.loads(log.details) if log.details else None,
                    timestamp=log.timestamp,
                )
                for log in log_models
            ]
