"""Telegram-related Pydantic models.

This module contains typed models for Telegram bot operations,
replacing dict usage with proper Pydantic models for type safety.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.models.base import JsonModel


class TelegramIdentity(JsonModel):
    """Extracted Telegram user identity information.

    Attributes:
        user_id: Primary system identifier (Telegram username).
        telegram_user_id: Numeric Telegram user ID.
        telegram_username: Telegram username.
        display_name: Display name for logs.
    """

    user_id: str | None = None
    telegram_user_id: str | None = None
    telegram_username: str | None = None
    display_name: str = "unknown"


class TelegramAttachment(JsonModel):
    """File attachment metadata from Telegram.

    Attributes:
        file_id: Telegram file ID.
        file_name: Sanitized filename.
        file_path: Local path to downloaded file.
        mime_type: MIME type.
        file_size: Size in bytes.
        is_image: Whether file is an image.
    """

    file_id: str
    file_name: str
    file_path: str
    mime_type: str | None = None
    file_size: int
    is_image: bool = False


class TelegramMessage(JsonModel):
    """Message payload for SQS queue processing.

    Attributes:
        user_id: Telegram user ID.
        message: Message text.
        chat_id: Telegram chat ID for responses.
        timestamp: Message timestamp.
        attachments: Optional list of file attachments.
    """

    user_id: str
    message: str
    chat_id: int
    timestamp: str
    attachments: list[TelegramAttachment] = []

    def to_queue_payload(self) -> dict[str, Any]:
        """Convert to SQS message payload format."""
        return {
            "user_id": self.user_id,
            "message": self.message,
            "chat_id": self.chat_id,
            "timestamp": self.timestamp,
            "attachments": [
                {
                    "file_id": att.file_id,
                    "file_name": att.file_name,
                    "file_path": att.file_path,
                    "mime_type": att.mime_type,
                    "file_size": att.file_size,
                    "is_image": att.is_image,
                }
                for att in self.attachments
            ],
        }

    @classmethod
    def from_queue_payload(cls, payload: dict[str, Any]) -> TelegramMessage:
        """Create from SQS message payload."""
        attachments_data = payload.get("attachments", [])
        attachments = [
            TelegramAttachment(
                file_id=att["file_id"],
                file_name=att["file_name"],
                file_path=att["file_path"],
                mime_type=att.get("mime_type"),
                file_size=att["file_size"],
                is_image=att.get("is_image", False),
            )
            for att in attachments_data
        ]
        return cls(
            user_id=payload["user_id"],
            message=payload["message"],
            chat_id=payload["chat_id"],
            timestamp=payload["timestamp"],
            attachments=attachments,
        )


class BotInfo(JsonModel):
    """Bot information.

    Attributes:
        id: Bot ID.
        username: Bot username.
        first_name: Bot first name.
    """

    id: int
    username: str
    first_name: str | None = None


class EnqueueResult(JsonModel):
    """Result of enqueuing a message.

    Attributes:
        success: Whether enqueuing succeeded.
        queue_url: The queue URL used.
        message_id: Optional SQS message ID.
    """

    success: bool
    queue_url: str
    message_id: str | None = None
