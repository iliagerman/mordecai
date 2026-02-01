"""Telegram message sending utilities.

This module handles sending responses and files to Telegram users.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from telegram.error import TelegramError

logger = logging.getLogger(__name__)


class TelegramMessageSender:
    """Handles sending messages and files to Telegram.

    Provides methods for sending text responses, documents, and photos
    with proper formatting and error handling.
    """

    def __init__(self, bot: Any):
        """Initialize the message sender.

        Args:
            bot: Telegram bot instance from python-telegram-bot.
        """
        self.bot = bot

    async def send_response(self, chat_id: int, response: str) -> None:
        """Send a response message to a Telegram chat.

        Args:
            chat_id: Telegram chat ID to send to.
            response: Response text to send.

        Requirements:
            - 11.3: Send agent responses back to user
        """
        from telegram.constants import ParseMode
        from app.telegram.response_formatter import TelegramResponseFormatter

        # Convert markdown to Telegram HTML format (more reliable than MarkdownV2)
        formatter = TelegramResponseFormatter()
        formatted = formatter.format_for_html(response)

        try:
            # Split long messages (Telegram has 4096 char limit)
            max_length = 4096
            if len(formatted) <= max_length:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=formatted,
                    parse_mode=ParseMode.HTML,
                )
            else:
                # Split into chunks
                for i in range(0, len(formatted), max_length):
                    chunk = formatted[i : i + max_length]
                    await self.bot.send_message(
                        chat_id=chat_id,
                        text=chunk,
                        parse_mode=ParseMode.HTML,
                    )

            logger.debug("Response sent to chat %s", chat_id)

        except Exception as e:
            logger.error("Failed to send response to chat %s: %s", chat_id, e)
            # Fallback to plain text if markdown parsing fails
            try:
                await self.bot.send_message(chat_id=chat_id, text=response)
            except Exception as fallback_error:
                logger.error("Fallback send also failed: %s", fallback_error)

    async def send_file(
        self,
        chat_id: int,
        file_path: str | Path,
        caption: str | None = None,
    ) -> bool:
        """Send a file to a Telegram chat.

        Sends the file as a document. For images under 10MB, use
        send_photo() instead for inline preview.

        Args:
            chat_id: Telegram chat ID to send to.
            file_path: Path to the file to send.
            caption: Optional caption for the file.

        Returns:
            True if send succeeded, False otherwise.

        Requirements:
            - 5.2: Send generated files back to user via Telegram
            - 5.3: Support sending documents up to 50MB
            - 5.4: Include appropriate filename and caption
        """
        max_retries = 2
        file_path = Path(file_path)

        if not file_path.exists():
            logger.error("File not found: %s", file_path)
            return False

        file_size = file_path.stat().st_size

        # Check Telegram limit (50MB for bots)
        if file_size > 50 * 1024 * 1024:
            await self.send_response(
                chat_id,
                f"❌ File too large to send "
                f"({file_size // 1024 // 1024}MB). "
                "Telegram limit is 50MB.",
            )
            return False

        for attempt in range(max_retries):
            try:
                with open(file_path, "rb") as f:
                    await self.bot.send_document(
                        chat_id=chat_id,
                        document=f,
                        filename=file_path.name,
                        caption=caption,
                    )
                logger.info("File sent to chat %s: %s", chat_id, file_path)
                return True

            except TelegramError as e:
                logger.warning(
                    "File send attempt %d failed: %s",
                    attempt + 1,
                    e,
                )
                if attempt == max_retries - 1:
                    await self.send_response(
                        chat_id,
                        "❌ Failed to send file after retrying. Please try again later.",
                    )
                    return False

        return False

    async def send_photo(
        self,
        chat_id: int,
        photo_path: str | Path,
        caption: str | None = None,
    ) -> bool:
        """Send a photo to a Telegram chat for inline preview.

        For images 10MB or larger, falls back to send_file().

        Args:
            chat_id: Telegram chat ID to send to.
            photo_path: Path to the image file.
            caption: Optional caption for the photo.

        Returns:
            True if send succeeded, False otherwise.

        Requirements:
            - 5.5: Send images as photo for inline preview when under 10MB
        """
        photo_path = Path(photo_path)

        if not photo_path.exists():
            logger.error("Photo not found: %s", photo_path)
            return False

        file_size = photo_path.stat().st_size

        # Route based on size (Requirement 5.5)
        # Images 10MB+ should be sent as documents
        if file_size >= 10 * 1024 * 1024:
            return await self.send_file(chat_id, photo_path, caption)

        max_retries = 2

        for attempt in range(max_retries):
            try:
                with open(photo_path, "rb") as f:
                    await self.bot.send_photo(
                        chat_id=chat_id,
                        photo=f,
                        caption=caption,
                    )
                logger.info("Photo sent to chat %s: %s", chat_id, photo_path)
                return True

            except TelegramError as e:
                logger.warning(
                    "Photo send attempt %d failed: %s",
                    attempt + 1,
                    e,
                )
                if attempt == max_retries - 1:
                    # Fall back to sending as document
                    logger.info("Falling back to document send")
                    return await self.send_file(chat_id, photo_path, caption)

        return False
