"""Per-user SQS queue management.

This module manages per-user SQS queues for asynchronous message processing.
Uses LocalStack for local development.

Requirements:
- 12.1: Create a dedicated SQS_Queue for each user
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mypy_boto3_sqs import SQSClient

logger = logging.getLogger(__name__)


class SQSQueueManager:
    """Manages per-user SQS queues.

    Each user gets a dedicated queue for message processing.
    Queues are created on-demand when a user first sends a message.

    Requirements:
        - 12.1: Create a dedicated SQS_Queue for each user
    """

    def __init__(
        self,
        sqs_client: "SQSClient",
        queue_prefix: str = "agent-user-",
    ) -> None:
        """Initialize the queue manager.

        Args:
            sqs_client: Boto3 SQS client (configured for LocalStack in dev).
            queue_prefix: Prefix for queue names (default: "agent-user-").
        """
        self.sqs_client = sqs_client
        self.queue_prefix = queue_prefix
        self.user_queues: dict[str, str] = {}  # user_id -> queue_url

    def get_or_create_queue(self, user_id: str) -> str:
        """Get existing queue or create new one for user.

        Creates a dedicated SQS queue for the user if one doesn't exist.
        Queue configuration:
        - VisibilityTimeout: 900 seconds (15 minutes) for long-running skills
        - MessageRetentionPeriod: 86400 seconds (24 hours)

        Args:
            user_id: Unique identifier for the user.

        Returns:
            Queue URL for the user's queue.

        Requirements:
            - 12.1: Create a dedicated SQS_Queue for each user
        """
        if user_id in self.user_queues:
            logger.debug(
                "Returning cached queue URL for user_id=%s", user_id
            )
            return self.user_queues[user_id]

        queue_name = f"{self.queue_prefix}{user_id}"
        logger.info("Creating SQS queue: %s", queue_name)

        response = self.sqs_client.create_queue(
            QueueName=queue_name,
            Attributes={
                "VisibilityTimeout": "900",  # 15 minutes for long-running skills
                "MessageRetentionPeriod": "86400",  # 24 hours
            },
        )

        queue_url = response["QueueUrl"]
        self.user_queues[user_id] = queue_url

        logger.info(
            "Created queue for user_id=%s: %s", user_id, queue_url
        )

        return queue_url

    def get_all_queue_urls(self) -> list[str]:
        """Return all active user queue URLs.

        Returns:
            List of queue URLs for all registered users.
        """
        return list(self.user_queues.values())

    def get_queue_url_for_user(self, user_id: str) -> str | None:
        """Get queue URL for a specific user without creating.

        Args:
            user_id: Unique identifier for the user.

        Returns:
            Queue URL if exists, None otherwise.
        """
        return self.user_queues.get(user_id)

    def remove_user_queue(self, user_id: str) -> bool:
        """Remove a user's queue from tracking.

        Note: This does not delete the queue from SQS, only removes
        it from the local cache. Use delete_queue() to fully remove.

        Args:
            user_id: Unique identifier for the user.

        Returns:
            True if queue was removed, False if not found.
        """
        if user_id in self.user_queues:
            del self.user_queues[user_id]
            logger.info("Removed queue tracking for user_id=%s", user_id)
            return True
        return False

    def delete_queue(self, user_id: str) -> bool:
        """Delete a user's queue from SQS.

        Args:
            user_id: Unique identifier for the user.

        Returns:
            True if queue was deleted, False if not found.
        """
        queue_url = self.user_queues.get(user_id)
        if not queue_url:
            logger.warning(
                "No queue found for user_id=%s to delete", user_id
            )
            return False

        try:
            self.sqs_client.delete_queue(QueueUrl=queue_url)
            del self.user_queues[user_id]
            logger.info(
                "Deleted queue for user_id=%s: %s", user_id, queue_url
            )
            return True
        except Exception as e:
            logger.error(
                "Failed to delete queue for user_id=%s: %s", user_id, e
            )
            return False

    def get_user_count(self) -> int:
        """Get the number of users with active queues.

        Returns:
            Number of users with registered queues.
        """
        return len(self.user_queues)
