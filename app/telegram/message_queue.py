"""Telegram message queue operations.

This module handles SQS message enqueuing for async processing.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.telegram.models import TelegramAttachment, TelegramMessage

logger = logging.getLogger(__name__)


class MessageQueueHandler:
    """Handles SQS message enqueuing for Telegram bot.

    Enqueues messages with optional attachments for async processing
    by the agent worker.
    """

    def __init__(self, sqs_client: Any, queue_manager: Any):
        """Initialize the queue handler.

        Args:
            sqs_client: Boto3 SQS client.
            queue_manager: Queue manager for per-user queues.
        """
        self.sqs_client = sqs_client
        self.queue_manager = queue_manager

    def enqueue_message(
        self,
        user_id: str,
        chat_id: int,
        message: str,
        onboarding_context: dict[str, str | None] | None = None,
    ) -> str:
        """Enqueue a message to the user's SQS queue for processing.

        Args:
            user_id: Telegram user ID.
            chat_id: Telegram chat ID for responses.
            message: Message text to process.
            onboarding_context: Optional onboarding context (soul.md, id.md content)
                if this is the user's first interaction.

        Returns:
            The queue URL used.

        Requirements:
            - 11.2: Forward messages to Agent for processing
            - 12.2: Enqueue message to user's SQS_Queue
        """
        logger.debug("Enqueueing message for user %s", user_id)

        # Get or create the user's queue
        queue_url = self.queue_manager.get_or_create_queue(user_id)

        # Create message payload
        payload = {
            "user_id": user_id,
            "message": message,
            "chat_id": chat_id,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        # Add onboarding context if present (first interaction)
        if onboarding_context:
            payload["onboarding"] = onboarding_context
            logger.info(
                "Message enqueued with onboarding context for user %s (first interaction)",
                user_id,
            )

        # Send to SQS queue
        self.sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(payload),
        )

        logger.info("Message enqueued for user %s to queue %s", user_id, queue_url)
        return queue_url

    def enqueue_message_with_attachments(
        self,
        user_id: str,
        chat_id: int,
        message: str,
        attachments: list[Any],
    ) -> str:
        """Enqueue a message with file attachments to SQS queue.

        Args:
            user_id: Telegram user ID.
            chat_id: Telegram chat ID for responses.
            message: Message text (caption) to process.
            attachments: List of attachment metadata objects.

        Returns:
            The queue URL used.

        Requirements:
            - 6.1: Extend SQS message payload for attachments
            - 6.3: Process file messages through same queue
            - 6.4: Support messages with text and attachments
            - 6.5: Process each attachment separately in array
        """
        logger.debug(
            "Enqueueing message with %d attachments for user %s",
            len(attachments),
            user_id,
        )

        # Get or create the user's queue
        queue_url = self.queue_manager.get_or_create_queue(user_id)

        # Build attachments array for payload (Requirement 6.1, 6.5)
        attachments_data = [
            {
                "file_id": att.file_id,
                "file_name": att.file_name,
                "file_path": att.file_path,
                "mime_type": att.mime_type,
                "file_size": att.file_size,
                "is_image": att.is_image,
            }
            for att in attachments
        ]

        # Create extended message payload (Requirement 6.1)
        payload = {
            "user_id": user_id,
            "message": message,
            "chat_id": chat_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "attachments": attachments_data,
        }

        # Send to SQS queue
        self.sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(payload),
        )

        logger.info(
            "Message with %d attachments enqueued for user %s",
            len(attachments),
            user_id,
        )
        return queue_url
