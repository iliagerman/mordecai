"""Telegram message sending utilities.

This module handles sending responses and files to Telegram users.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

from telegram import InputFile
from telegram.error import TelegramError

logger = logging.getLogger(__name__)


class TelegramBotProtocol(Protocol):
    # NOTE: Protocol parameter types must not be *wider* than the real bot's
    # signature, otherwise structural typing fails (ExtBot is stricter than `object`).
    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        parse_mode: Any | None = None,
    ) -> Any: ...

    async def send_document(
        self,
        chat_id: int | str,
        document: Any,
        caption: str | None = None,
    ) -> Any: ...

    async def send_photo(
        self,
        chat_id: int | str,
        photo: Any,
        caption: str | None = None,
    ) -> Any: ...

    async def send_chat_action(
        self,
        chat_id: int | str,
        action: str,
    ) -> Any: ...


class TelegramMessageSender:
    """Handles sending messages and files to Telegram.

    Provides methods for sending text responses, documents, and photos
    with proper formatting and error handling.
    """

    def __init__(self, bot: TelegramBotProtocol):
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

        # Telegram has a hard 4096-character limit for sendMessage text.
        #
        # IMPORTANT:
        # - Splitting *after* formatting (HTML) can break tags across boundaries.
        #   That can cause Telegram parse errors and lead to retries that look like
        #   duplicated messages (especially for long onboarding messages).
        # - Therefore we chunk the *raw* text first, then format each chunk.
        telegram_max_len = 4096
        # Leave room for HTML markup added by the formatter.
        raw_chunk_len = 3500

        def _split_raw(text: str, max_len: int) -> list[str]:
            if not text:
                return [""]
            lines = text.splitlines(keepends=True)
            chunks: list[str] = []
            current = ""

            for line in lines:
                if len(current) + len(line) <= max_len:
                    current += line
                    continue

                if current:
                    chunks.append(current)
                    current = ""

                # If the next line itself is too big, hard-slice it.
                if len(line) > max_len:
                    for i in range(0, len(line), max_len):
                        part = line[i : i + max_len]
                        if len(part) == max_len:
                            chunks.append(part)
                        else:
                            current = part
                else:
                    current = line

            if current:
                chunks.append(current)
            return chunks

        formatter = TelegramResponseFormatter()
        raw_chunks = _split_raw(response, raw_chunk_len)

        for raw in raw_chunks:
            try:
                formatted = formatter.format_for_html(raw)
                # Guard: if formatting expands beyond Telegram limit, fall back.
                if len(formatted) > telegram_max_len:
                    raise ValueError(
                        f"Formatted chunk exceeds Telegram limit ({len(formatted)}>{telegram_max_len})"
                    )

                await self.bot.send_message(
                    chat_id=chat_id,
                    text=formatted,
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                logger.warning(
                    "Failed to send formatted chunk to chat %s (falling back to plain text): %s",
                    chat_id,
                    e,
                )
                try:
                    await self.bot.send_message(chat_id=chat_id, text=raw)
                except Exception as fallback_error:
                    logger.error(
                        "Fallback plain-text send failed for chat %s: %s",
                        chat_id,
                        fallback_error,
                    )

        logger.debug("Response sent to chat %s", chat_id)

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
                    document = InputFile(f, filename=file_path.name)
                    await self.bot.send_document(
                        chat_id=chat_id,
                        document=document,
                        caption=caption,
                    )
                logger.info("File sent to chat %s: %s", chat_id, file_path)
                return True

            except (TelegramError, OSError, ValueError, TypeError) as e:
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
                    photo = InputFile(f, filename=photo_path.name)
                    await self.bot.send_photo(
                        chat_id=chat_id,
                        photo=photo,
                        caption=caption,
                    )
                logger.info("Photo sent to chat %s: %s", chat_id, photo_path)
                return True

            except (TelegramError, OSError, ValueError, TypeError) as e:
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

    async def send_chat_action(
        self,
        chat_id: int,
        action: str = "typing",
    ) -> bool:
        """Send a chat action to show bot activity.

        Chat actions like 'typing' or 'upload_document' show the user
        that the bot is working on their request. Actions expire after
        about 5 seconds and need to be resent.

        Args:
            chat_id: Telegram chat ID to send to.
            action: Chat action type. Common values:
                - 'typing': Bot is typing a message
                - 'upload_document': Bot is uploading a file
                - 'upload_photo': Bot is uploading a photo
                - 'record_video': Bot is recording a video
                - 'record_audio': Bot is recording audio
                - 'find_location': Bot is sharing location

        Returns:
            True if send succeeded, False otherwise.
        """
        from telegram.constants import ChatAction

        # Map string action to ChatAction enum (only valid actions from telegram>=21.x)
        action_map = {
            "typing": ChatAction.TYPING,
            "upload_document": ChatAction.UPLOAD_DOCUMENT,
            "upload_photo": ChatAction.UPLOAD_PHOTO,
            "upload_video": ChatAction.UPLOAD_VIDEO,
            "upload_voice": ChatAction.UPLOAD_VOICE,
            "upload_video_note": ChatAction.UPLOAD_VIDEO_NOTE,
            "record_video": ChatAction.RECORD_VIDEO,
            "record_video_note": ChatAction.RECORD_VIDEO_NOTE,
            "record_voice": ChatAction.RECORD_VOICE,
            "find_location": ChatAction.FIND_LOCATION,
            "choose_sticker": ChatAction.CHOOSE_STICKER,
        }

        chat_action = action_map.get(action, ChatAction.TYPING)

        try:
            await self.bot.send_chat_action(
                chat_id=chat_id,
                action=chat_action,
            )
            logger.debug("Sent chat action '%s' to chat %s", action, chat_id)
            return True
        except Exception as e:
            logger.warning("Failed to send chat action to chat %s: %s", chat_id, e)
            return False
