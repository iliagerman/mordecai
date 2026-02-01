"""Telegram message handlers.

This module handles incoming Telegram updates including:
- Command handlers (start, help, new, logs, skills, add_skill, delete_skill)
- Text message handler
- Document and photo attachment handlers
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from telegram import Update, PhotoSize
from telegram.error import TelegramError

from app.enums import LogSeverity
from app.security.whitelist import DEFAULT_FORBIDDEN_DETAIL, is_whitelisted, live_allowed_users

if TYPE_CHECKING:
    from app.telegram.models import TelegramAttachment
    from app.services.logging_service import LoggingService
    from app.services.file_service import FileService
    from app.services.skill_service import SkillService

logger = logging.getLogger(__name__)


class TelegramMessageHandlers:
    """Handles incoming Telegram updates for the bot.

    Processes commands, text messages, and file attachments.
    """

    def __init__(
        self,
        config: Any,
        logging_service: "LoggingService",
        skill_service: "SkillService",
        file_service: "FileService",
        command_parser: Any,
        bot_application: Any,
        get_allowed_users: callable,
    ):
        """Initialize the message handlers.

        Args:
            config: Application configuration.
            logging_service: Logging service for activity logs.
            skill_service: Skill service for skill operations.
            file_service: File service for attachment handling.
            command_parser: Command parser instance.
            bot_application: Telegram bot application.
            get_allowed_users: Function to get live allowed users.
        """
        self.config = config
        self.logging_service = logging_service
        self.skill_service = skill_service
        self.file_service = file_service
        self.command_parser = command_parser
        self.bot = bot_application.bot
        self._get_allowed_users_live = get_allowed_users

    def extract_telegram_identity(self, update: Update) -> tuple:
        """Extract user identity from a Telegram update.

        Returns:
            (user_id, telegram_user_id, telegram_username, display_name)

        Notes:
            - In this system, the *primary* user identifier (user_id) is the Telegram username.
              We do not use numeric IDs as user identifiers.
            - telegram_user_id is retained for whitelist checks and diagnostics.
        """
        user = getattr(update, "effective_user", None)

        telegram_user_id: str | None = None
        telegram_username: str | None = None
        display_name: str | None = None

        try:
            raw_id = getattr(user, "id", None)
            if raw_id is not None:
                telegram_user_id = str(raw_id)
        except Exception:
            telegram_user_id = None

        try:
            telegram_username = getattr(user, "username", None) or None
        except Exception:
            telegram_username = None

        try:
            display_name = getattr(user, "first_name", None) or None
        except Exception:
            display_name = None

        # Primary system identifier: username only.
        user_id = telegram_username

        # Defensive display name for logs.
        fallback = telegram_username or telegram_user_id or "unknown"
        return user_id, telegram_user_id, telegram_username, (display_name or fallback)

    async def reject_if_missing_username(self, chat_id: int) -> bool:
        """Return True if request should be rejected due to missing Telegram username."""
        from app.telegram.message_sender import TelegramMessageSender

        await TelegramMessageSender(self.bot).send_response(
            chat_id,
            (
                "❌ Your Telegram account must have a username to use this bot.\n\n"
                "Please set a username in Telegram Settings → Username, then try again."
            ),
        )
        return True

    async def reject_if_not_whitelisted(
        self,
        telegram_user_id: str,
        telegram_username: str | None,
        chat_id: int,
    ) -> bool:
        """Return True if request should be rejected due to whitelist."""
        # _get_allowed_users_live may be a LiveAllowedUsers object with .get() or a plain callable
        if hasattr(self._get_allowed_users_live, "get"):
            allowed = self._get_allowed_users_live.get()
        else:
            allowed = self._get_allowed_users_live()
        whitelisted = is_whitelisted(telegram_user_id, allowed) or (
            telegram_username is not None and is_whitelisted(telegram_username, allowed)
        )
        if whitelisted:
            return False

        # Log to both DB-backed activity logs and server logs.
        logger.warning(
            "Telegram user rejected by whitelist (chat_id=%s, telegram_user_id=%s, username=%s)",
            chat_id,
            telegram_user_id,
            telegram_username,
        )
        try:
            await self.logging_service.log_action(
                # Primary identifier in this system is the Telegram username.
                # We avoid storing numeric identifiers as user_id.
                user_id=telegram_username or "unknown",
                action="Rejected Telegram message: user not whitelisted",
                severity=LogSeverity.WARNING,
                details={
                    "chat_id": chat_id,
                    "telegram_user_id": telegram_user_id,
                    "telegram_username": telegram_username,
                },
            )
        except Exception:
            # Never block rejection response due to logging failures.
            logger.exception("Failed to persist whitelist rejection log")

        # Keep the human guidance stable across HTTP + Telegram.
        await self._send_response(chat_id, f"403 Forbidden, {DEFAULT_FORBIDDEN_DETAIL}")
        return True

    async def _send_response(self, chat_id: int, response: str) -> None:
        """Send a text response to a Telegram chat.

        Args:
            chat_id: Telegram chat ID to send to.
            response: Response text to send.
        """
        from app.telegram.message_sender import TelegramMessageSender

        await TelegramMessageSender(self.bot).send_response(chat_id, response)

    def migrate_legacy_skill_folder(self, telegram_user_id: str | None, user_id: str) -> None:
        """One-way migration: move skills/<numeric_id>/ -> skills/<username>/.        No backward-compat behavior is kept after migration.
        """
        if not telegram_user_id:
            return
        try:
            migrated = self.skill_service.migrate_user_skills_dir(
                legacy_user_id=telegram_user_id,
                user_id=user_id,
            )
            if migrated:
                logger.info(
                    "Migrated legacy skills folder from %s to %s",
                    telegram_user_id,
                    user_id,
                )
        except Exception:
            # Never block user interactions due to migration issues.
            logger.exception(
                "Failed to migrate legacy skills folder from %s to %s",
                telegram_user_id,
                user_id,
            )

    async def handle_start_command(
        self, update: Update, context: Any
    ) -> None:
        """Handle /start command.

        Sends a welcome message to new users.

        Args:
            update: Telegram update object.
            context: Callback context.
        """
        chat = update.effective_chat
        if chat is None:
            logger.warning("Telegram update missing effective_chat for /start")
            return
        chat_id = chat.id

        user_id, telegram_user_id, username, _ = self.extract_telegram_identity(update)
        if await self.reject_if_not_whitelisted(telegram_user_id or "unknown", username, chat_id):
            return
        if not user_id:
            await self.reject_if_missing_username(chat_id)
            return

        logger.info("User %s started the bot", user_id)

        welcome_message = (
            "Welcome to Mordecai! 🤖\n\n"
            "I'm your AI assistant. You can:\n"
            "- Send me any message and I'll help you\n"
            "- Use 'new' to start a fresh conversation\n"
            "- Use 'logs' to see recent activity\n"
            "- Use 'install skill <url>' to add capabilities\n"
            "- Use 'uninstall skill <name>' to remove skills\n"
            "- Use 'help' for more information\n\n"
            "How can I help you today?"
        )

        await self._send_response(chat_id, welcome_message)

        # Log the start action
        try:
            await self.logging_service.log_action(
                user_id=user_id,
                action="Started bot interaction",
                severity=LogSeverity.INFO,
            )
        except Exception:
            logger.exception("Failed to persist start interaction log")

    async def handle_help_command(
        self, update: Update, context: Any
    ) -> None:
        """Handle /help command.

        Sends help text with available commands.

        Args:
            update: Telegram update object.
            context: Callback context.

        Requirements:
            - 11.5: Support basic commands (help)
        """
        chat = update.effective_chat
        if chat is None:
            logger.warning("Telegram update missing effective_chat for /help")
            return
        chat_id = chat.id

        user_id, telegram_user_id, username, _ = self.extract_telegram_identity(update)
        if await self.reject_if_not_whitelisted(telegram_user_id or "unknown", username, chat_id):
            return
        if not user_id:
            await self.reject_if_missing_username(chat_id)
            return

        help_text = self.command_parser.get_help_text()
        await self._send_response(chat_id, help_text)

    async def handle_new_command(self, update: Update, context: Any, execute_new: callable) -> None:
        """Handle /new command.

        Creates a new session for the user.

        Args:
            update: Telegram update object.
            context: Callback context.
            execute_new: Callback to execute new command.

        Requirements:
            - 11.5: Support basic commands (new)
            - 11.6: Parse and execute appropriate actions
        """
        chat = update.effective_chat
        if chat is None:
            logger.warning("Telegram update missing effective_chat for /new")
            return
        chat_id = chat.id

        user_id, telegram_user_id, username, _ = self.extract_telegram_identity(update)
        if await self.reject_if_not_whitelisted(telegram_user_id or "unknown", username, chat_id):
            return
        if not user_id:
            await self.reject_if_missing_username(chat_id)
            return
        await execute_new(user_id, chat_id)

    async def handle_logs_command(self, update: Update, context: Any, execute_logs: callable) -> None:
        """Handle /logs command.

        Shows recent activity logs for the user.

        Args:
            update: Telegram update object.
            context: Callback context.
            execute_logs: Callback to execute logs command.

        Requirements:
            - 11.5: Support basic commands (logs)
            - 11.6: Parse and execute appropriate actions
        """
        chat = update.effective_chat
        if chat is None:
            logger.warning("Telegram update missing effective_chat for /logs")
            return
        chat_id = chat.id

        user_id, telegram_user_id, username, _ = self.extract_telegram_identity(update)
        if await self.reject_if_not_whitelisted(telegram_user_id or "unknown", username, chat_id):
            return
        if not user_id:
            await self.reject_if_missing_username(chat_id)
            return
        await execute_logs(user_id, chat_id)

    async def handle_skills_command(self, update: Update, context: Any) -> None:
        """Handle /skills command - list all installed skills."""
        chat = update.effective_chat
        if chat is None:
            logger.warning("Telegram update missing effective_chat for /skills")
            return
        chat_id = chat.id

        user_id, telegram_user_id, username, _ = self.extract_telegram_identity(update)

        if await self.reject_if_not_whitelisted(telegram_user_id or "unknown", username, chat_id):
            return

        if not user_id:
            await self.reject_if_missing_username(chat_id)
            return

        self.migrate_legacy_skill_folder(telegram_user_id, user_id)

        skills = self.skill_service.list_skills(user_id)

        if not skills:
            await self._send_response(chat_id, "No skills installed yet.")
            return

        skill_list = "\n".join(f"• {skill}" for skill in skills)
        await self._send_response(chat_id, f"📦 Installed skills:\n\n{skill_list}")

    async def handle_add_skill_command(
        self, update: Update, context: Any, execute_install: callable
    ) -> None:
        """Handle /add_skill <url> command - install a skill from URL."""
        chat = update.effective_chat
        if chat is None:
            logger.warning("Telegram update missing effective_chat for /add_skill")
            return
        chat_id = chat.id

        user_id, telegram_user_id, username, _ = self.extract_telegram_identity(update)

        if await self.reject_if_not_whitelisted(telegram_user_id or "unknown", username, chat_id):
            return

        if not user_id:
            await self.reject_if_missing_username(chat_id)
            return

        self.migrate_legacy_skill_folder(telegram_user_id, user_id)

        if not context.args:
            await self._send_response(
                chat_id,
                "Usage: /add_skill <url>\n\n"
                "Example: /add_skill https://github.com/user/repo/tree/main/skill",
            )
            return

        url = context.args[0]
        await execute_install(user_id, chat_id, url)

    async def handle_delete_skill_command(
        self, update: Update, context: Any, execute_uninstall: callable
    ) -> None:
        """Handle /delete_skill <name> command - remove an installed skill."""
        chat = update.effective_chat
        if chat is None:
            logger.warning("Telegram update missing effective_chat for /delete_skill")
            return
        chat_id = chat.id

        user_id, telegram_user_id, username, _ = self.extract_telegram_identity(update)

        if await self.reject_if_not_whitelisted(telegram_user_id or "unknown", username, chat_id):
            return

        if not user_id:
            await self.reject_if_missing_username(chat_id)
            return

        self.migrate_legacy_skill_folder(telegram_user_id, user_id)

        if not context.args:
            await self._send_response(
                chat_id, "Usage: /delete_skill <name>\n\nUse /skills to see installed skills."
            )
            return

        skill_name = context.args[0]
        await execute_uninstall(user_id, chat_id, skill_name)

    async def handle_message(
        self, update: Update, context: Any, execute_command: callable
    ) -> None:
        """Handle incoming text messages.

        Parses the message for commands and either executes them directly
        or forwards to the agent via SQS queue.

        Args:
            update: Telegram update object.
            context: Callback context.
            execute_command: Callback to execute parsed command.

        Requirements:
            - 11.2: Forward messages to Agent for processing
            - 11.4: Identify users by Telegram user ID
            - 11.6: Parse and execute appropriate actions
        """
        if not update.message or not update.message.text:
            return

        chat = update.effective_chat
        if chat is None:
            logger.warning("Telegram update missing effective_chat for message")
            return
        chat_id = chat.id

        user_id, telegram_user_id, username, display_name = self.extract_telegram_identity(update)
        message_text = update.message.text

        if await self.reject_if_not_whitelisted(telegram_user_id or "unknown", username, chat_id):
            return

        if not user_id:
            await self.reject_if_missing_username(chat_id)
            return

        self.migrate_legacy_skill_folder(telegram_user_id, user_id)

        if len(message_text) > 50:
            preview = message_text[:50] + "..."
        else:
            preview = message_text

        logger.info(
            "Received message from %s (@%s): %s", display_name, username or user_id, preview
        )

        # Parse the message for commands
        parsed = self.command_parser.parse(message_text)

        # Execute based on command type
        await execute_command(parsed, user_id, chat_id, message_text)

    async def handle_document(
        self,
        update: Update,
        context: Any,
        enqueue_with_attachments: callable,
    ) -> None:
        """Handle incoming document attachments.

        Validates the document, downloads it, and enqueues a message
        with attachment metadata for agent processing.

        Args:
            update: Telegram update object.
            context: Callback context.
            enqueue_with_attachments: Callback to enqueue with attachments.

        Requirements:
            - 1.1: Download document attachments from Telegram
            - 1.3: Extract file metadata from Telegram message
            - 1.4: Forward file path and metadata to agent
            - 1.5: Support common document types
            - 1.6: Reject files exceeding maximum size
            - 1.7: Log file receipt for auditing
            - 6.2: Add message handler for document filter
        """
        if not update.message or not update.message.document:
            return

        chat = update.effective_chat
        if chat is None:
            logger.warning("Telegram update missing effective_chat for document")
            return
        chat_id = chat.id

        user_id, telegram_user_id, username, _ = self.extract_telegram_identity(update)
        document = update.message.document
        caption = update.message.caption or ""

        if await self.reject_if_not_whitelisted(telegram_user_id or "unknown", username, chat_id):
            return

        if not user_id:
            await self.reject_if_missing_username(chat_id)
            return

        self.migrate_legacy_skill_folder(telegram_user_id, user_id)

        if document.file_size is None:
            await self._send_response(
                chat_id,
                "❌ Telegram did not provide a file size for this document; cannot accept it.",
            )
            return

        logger.info(
            "Received document from user %s: %s (%s bytes)",
            user_id,
            document.file_name,
            document.file_size,
        )

        # Validate file before download
        validation = self.file_service.validate_file(
            file_name=document.file_name or "unnamed_file",
            file_size=document.file_size,
            mime_type=document.mime_type,
        )

        if not validation.valid:
            await self._send_response(chat_id, f"❌ {validation.error_message}")
            await self.logging_service.log_action(
                user_id=user_id,
                action=f"Document rejected: {validation.error_message}",
                severity=LogSeverity.WARNING,
                details={
                    "file_name": document.file_name,
                    "file_size": document.file_size,
                },
            )
            return

        try:
            from app.telegram.models import TelegramAttachment

            # Download and store file
            metadata = await self.file_service.download_file(
                bot=self.bot,
                file_id=document.file_id,
                user_id=user_id,
                file_name=(validation.sanitized_name or document.file_name or "unnamed_file"),
                mime_type=document.mime_type,
            )

            # Convert to TelegramAttachment
            attachment = TelegramAttachment(
                file_id=document.file_id,
                file_name=metadata.file_name,
                file_path=metadata.file_path,
                mime_type=metadata.mime_type,
                file_size=metadata.file_size,
                is_image=False,
            )

            # Enqueue message with attachment
            await enqueue_with_attachments(
                user_id=user_id,
                chat_id=chat_id,
                message=caption,
                attachments=[attachment],
            )

            # Log the action (Requirement 1.7)
            await self.logging_service.log_action(
                user_id=user_id,
                action=f"Document received: {metadata.file_name}",
                severity=LogSeverity.INFO,
                details={
                    "file_name": metadata.file_name,
                    "file_size": metadata.file_size,
                    "mime_type": metadata.mime_type,
                },
            )

        except TelegramError as e:
            logger.error("Telegram download failed: %s", e)
            await self._send_response(
                chat_id,
                "❌ Failed to download file. Please try again.",
            )
            await self.logging_service.log_action(
                user_id=user_id,
                action="Document download failed",
                severity=LogSeverity.ERROR,
                details={"error": str(e)},
            )
        except Exception as e:
            logger.error("File handling error: %s", e)
            await self._send_response(
                chat_id,
                "❌ An error occurred processing your file.",
            )
            await self.logging_service.log_action(
                user_id=user_id,
                action="Document processing error",
                severity=LogSeverity.ERROR,
                details={"error": str(e)},
            )

    async def handle_photo(
        self,
        update: Update,
        context: Any,
        enqueue_with_attachments: callable,
    ) -> None:
        """Handle incoming photo attachments.

        Selects the highest resolution photo variant, downloads it,
        and enqueues a message with attachment metadata.

        Args:
            update: Telegram update object.
            context: Callback context.
            enqueue_with_attachments: Callback to enqueue with attachments.

        Requirements:
            - 2.1: Download image attachments from Telegram
            - 2.3: Extract image metadata when available
            - 2.5: Download highest resolution version
            - 2.6: Forward image path and metadata to agent
            - 6.2: Add message handler for photo filter
        """
        if not update.message or not update.message.photo:
            return

        chat = update.effective_chat
        if chat is None:
            logger.warning("Telegram update missing effective_chat for photo")
            return
        chat_id = chat.id

        user_id, telegram_user_id, username, _ = self.extract_telegram_identity(update)
        caption = update.message.caption or ""

        if await self.reject_if_not_whitelisted(telegram_user_id or "unknown", username, chat_id):
            return

        if not user_id:
            await self.reject_if_missing_username(chat_id)
            return

        self.migrate_legacy_skill_folder(telegram_user_id, user_id)

        # Select highest resolution photo (Requirement 2.5)
        # Photos are sorted by size, last one is largest
        photo = self.select_highest_resolution_photo(update.message.photo)

        if photo.file_size is None:
            await self._send_response(
                chat_id,
                "❌ Telegram did not provide a file size for this photo; cannot accept it.",
            )
            return

        logger.info(
            "Received photo from user %s: %dx%d (%d bytes)",
            user_id,
            photo.width,
            photo.height,
            photo.file_size,
        )

        # Validate file size
        max_bytes = self.config.max_file_size_mb * 1024 * 1024
        if photo.file_size > max_bytes:
            await self._send_response(
                chat_id,
                f"❌ Photo too large. Maximum size is {self.config.max_file_size_mb}MB",
            )
            return

        try:
            from app.telegram.models import TelegramAttachment

            # Generate filename for photo
            file_name = f"photo_{photo.file_unique_id}.jpg"

            # Download and store file
            metadata = await self.file_service.download_file(
                bot=self.bot,
                file_id=photo.file_id,
                user_id=user_id,
                file_name=file_name,
                mime_type="image/jpeg",
            )

            # Convert to TelegramAttachment
            attachment = TelegramAttachment(
                file_id=photo.file_id,
                file_name=metadata.file_name,
                file_path=metadata.file_path,
                mime_type="image/jpeg",
                file_size=metadata.file_size,
                is_image=True,
            )

            # Enqueue message with attachment
            await enqueue_with_attachments(
                user_id=user_id,
                chat_id=chat_id,
                message=caption,
                attachments=[attachment],
            )

            # Log the action
            await self.logging_service.log_action(
                user_id=user_id,
                action=f"Photo received: {photo.width}x{photo.height}",
                severity=LogSeverity.INFO,
                details={
                    "file_size": metadata.file_size,
                    "dimensions": f"{photo.width}x{photo.height}",
                },
            )

        except TelegramError as e:
            logger.error("Telegram photo download failed: %s", e)
            await self._send_response(
                chat_id,
                "❌ Failed to download photo. Please try again.",
            )
        except Exception as e:
            logger.error("Photo handling error: %s", e)
            await self._send_response(
                chat_id,
                "❌ An error occurred processing your photo.",
            )

    def select_highest_resolution_photo(
        self,
        photos: list[PhotoSize],
    ) -> PhotoSize:
        """Select the highest resolution photo from variants.

        Telegram sends multiple photo sizes. This selects the one
        with the largest file size (highest resolution).

        Args:
            photos: List of PhotoSize objects from Telegram.

        Returns:
            PhotoSize with the largest file size.

        Requirements:
            - 2.5: Download highest resolution version available
        """
        # Sort by file_size and return largest
        return max(photos, key=lambda p: p.file_size or 0)
