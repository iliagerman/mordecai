"""Logging business logic service.

This service handles all logging-related business logic, delegating
data access to the LogDAO. It provides methods for logging agent actions
and retrieving recent logs for users.

Requirements:
- 6.1: Display recent agent activity logs on "logs" command
- 6.2: Record all agent actions, tool calls, and errors
- 6.3: Support filtering logs by time range and severity
"""

import logging

from app.dao.log_dao import LogDAO
from app.enums import LogSeverity
from app.models.domain import LogEntry


class LoggingService:
    """Logging business logic.

    Handles logging of agent actions and retrieval of log history.
    All database operations are delegated to the LogDAO.
    """

    def __init__(self, log_dao: LogDAO) -> None:
        """Initialize the logging service.

        Args:
            log_dao: Data access object for log operations.
        """
        self.log_dao = log_dao
        self.logger = logging.getLogger("agent")

    async def log_action(
        self,
        user_id: str,
        action: str,
        severity: LogSeverity = LogSeverity.INFO,
        details: dict | None = None,
    ) -> LogEntry:
        """Log an agent action.

        Records the action to both the database (via LogDAO) and the
        Python logger for console/file output.

        Args:
            user_id: User identifier for the action.
            action: Description of the action being logged.
            severity: Log severity level (default: INFO).
            details: Optional additional details as a dict.

        Returns:
            Created LogEntry domain model.

        Requirements:
            - 6.2: Records all agent actions, tool calls, and errors
        """
        # Also log to Python logger for console/file output
        log_method = getattr(self.logger, severity.value)
        log_method(f"User {user_id}: {action}")

        return await self.log_dao.create(user_id, action, severity, details)

    async def get_recent_logs(
        self,
        user_id: str,
        hours: int = 24,
        severity: LogSeverity | None = None,
    ) -> list[LogEntry]:
        """Get recent logs for a user.

        Retrieves log entries from the specified time range, optionally
        filtered by severity level.

        Args:
            user_id: User identifier to get logs for.
            hours: Number of hours to look back (default: 24).
            severity: Optional severity filter.

        Returns:
            List of LogEntry domain models, ordered by timestamp descending.

        Requirements:
            - 6.1: Display recent agent activity logs
            - 6.3: Support filtering logs by time range and severity
        """
        return await self.log_dao.get_recent(user_id, hours, severity)
