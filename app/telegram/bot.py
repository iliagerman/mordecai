"""Telegram bot interface.

This module provides the Telegram bot interface for user interaction.
Messages are forwarded to the agent via SQS queues for async processing.

Requirements:
- 11.1: Connect to Telegram Bot API using configured bot token
- 11.2: Forward messages to Agent for processing
- 11.3: Send agent responses back to user
- 11.4: Identify users by Telegram user ID for session management
- 11.5: Support basic commands (new, logs, install skill, uninstall skill)
- 11.6: Parse and execute appropriate actions
- 1.1: Download document attachments from Telegram
- 2.1: Download image attachments from Telegram
- 6.2: Add message handlers for document and photo filters
"""

import json
import logging
from datetime import datetime, UTC
from pathlib import Path
from typing import TYPE_CHECKING

from telegram import PhotoSize, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import AgentConfig
from app.enums import CommandType, LogSeverity
from app.services.command_parser import CommandParser, ParsedCommand
from app.services.file_service import FileService
from app.services.logging_service import LoggingService
from app.services.skill_service import (
    SkillInstallError,
    SkillNotFoundError,
    SkillService,
)
from app.security.whitelist import DEFAULT_FORBIDDEN_DETAIL, is_whitelisted, live_allowed_users

if TYPE_CHECKING:
    from mypy_boto3_sqs import SQSClient

    from app.services.agent_service import AgentService
    from app.sqs.queue_manager import SQSQueueManager

logger = logging.getLogger(__name__)


class TelegramBotInterface:
    """Telegram bot interface for user interaction.

    Handles incoming messages from Telegram, parses commands, and either
    processes them directly (for simple commands) or forwards them to
    the agent via SQS queues for async processing.

    Requirements:
        - 11.1: Connect to Telegram Bot API using configured bot token
        - 11.4: Identify users by Telegram user ID for session management
    """

    def __init__(
        self,
        config: AgentConfig,
        sqs_client: "SQSClient",
        queue_manager: "SQSQueueManager",
        agent_service: "AgentService",
        logging_service: LoggingService,
        skill_service: SkillService,
        file_service: FileService | None = None,
        command_parser: CommandParser | None = None,
    ) -> None:
        """Initialize the Telegram bot interface.

        Args:
            config: Application configuration with bot token.
            sqs_client: Boto3 SQS client for message queuing.
            queue_manager: Queue manager for per-user queues.
            agent_service: Agent service for message processing.
            logging_service: Logging service for activity logs.
            skill_service: Skill service for skill management.
            file_service: File service for attachment handling.
            command_parser: Optional command parser (creates default if None).
        """
        self.config = config
        self.sqs_client = sqs_client
        self.queue_manager = queue_manager
        self.agent_service = agent_service
        self.logging_service = logging_service
        self.skill_service = skill_service
        self.file_service = file_service or FileService(config)
        self.command_parser = command_parser or CommandParser()

        # Hot-reloading whitelist: reads allowed_users from secrets.yml on demand.
        # This ensures updates to secrets.yml take effect immediately without
        # restarting the bot.
        self._allowed_users_live = live_allowed_users(config.secrets_path)

        # Build the application with the bot token
        self.application = Application.builder().token(config.telegram_bot_token).build()

        # Setup handlers
        self._setup_handlers()

        logger.info("TelegramBotInterface initialized")

    def _setup_handlers(self) -> None:
        """Setup message and command handlers.

        Registers handlers for:
        - /start and /help commands
        - /logs, /skills, /add_skill, /delete_skill commands
        - All text messages (for command parsing)
        - Document attachments
        - Photo attachments

        Requirements:
            - 11.5: Support basic commands
            - 11.6: Parse and execute appropriate actions
            - 6.2: Add message handlers for document and photo filters
        """
        # Command handlers for Telegram-style commands
        self.application.add_handler(CommandHandler("start", self._handle_start_command))
        self.application.add_handler(CommandHandler("help", self._handle_help_command))
        self.application.add_handler(CommandHandler("new", self._handle_new_command))
        self.application.add_handler(CommandHandler("logs", self._handle_logs_command))
        self.application.add_handler(CommandHandler("skills", self._handle_skills_command))
        self.application.add_handler(CommandHandler("add_skill", self._handle_add_skill_command))
        self.application.add_handler(
            CommandHandler("delete_skill", self._handle_delete_skill_command)
        )

        # File attachment handlers (Requirements: 1.1, 2.1, 6.2)
        if self.config.enable_file_attachments:
            self.application.add_handler(
                MessageHandler(filters.Document.ALL, self._handle_document)
            )
            self.application.add_handler(MessageHandler(filters.PHOTO, self._handle_photo))

        # General message handler for all text messages
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )

        logger.debug("Telegram handlers registered")

    def _extract_telegram_identity(
        self, update: Update
    ) -> tuple[str | None, str | None, str | None, str]:
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

    async def _reject_if_missing_username(self, chat_id: int) -> bool:
        """Return True if request should be rejected due to missing Telegram username."""
        await self.send_response(
            chat_id,
            (
                "❌ Your Telegram account must have a username to use this bot.\n\n"
                "Please set a username in Telegram Settings → Username, then try again."
            ),
        )
        return True

    def _migrate_legacy_skill_folder(self, telegram_user_id: str | None, user_id: str) -> None:
        """One-way migration: move skills/<numeric_id>/ -> skills/<username>/.

        No backward-compat behavior is kept after migration.
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

    async def _reject_if_not_whitelisted(
        self,
        telegram_user_id: str,
        telegram_username: str | None,
        chat_id: int,
    ) -> bool:
        """Return True if request should be rejected due to whitelist."""
        allowed = self._allowed_users_live
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
        await self.send_response(chat_id, f"403 Forbidden, {DEFAULT_FORBIDDEN_DETAIL}")
        return True

    async def _handle_start_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /start command.

        Sends a welcome message to new users.

        Args:
            update: Telegram update object.
            context: Callback context.
        """
        user_id, telegram_user_id, username, _ = self._extract_telegram_identity(update)
        if await self._reject_if_not_whitelisted(
            telegram_user_id or "unknown", username, update.effective_chat.id
        ):
            return
        if not user_id:
            await self._reject_if_missing_username(update.effective_chat.id)
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

        await self.send_response(update.effective_chat.id, welcome_message)

        # Log the start action
        try:
            await self.logging_service.log_action(
                user_id=user_id,
                action="Started bot interaction",
                severity=LogSeverity.INFO,
            )
        except Exception:
            logger.exception("Failed to persist start interaction log")

    async def _handle_help_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /help command.

        Sends help text with available commands.

        Args:
            update: Telegram update object.
            context: Callback context.

        Requirements:
            - 11.5: Support basic commands (help)
        """
        user_id, telegram_user_id, username, _ = self._extract_telegram_identity(update)
        if await self._reject_if_not_whitelisted(
            telegram_user_id or "unknown", username, update.effective_chat.id
        ):
            return
        if not user_id:
            await self._reject_if_missing_username(update.effective_chat.id)
            return

        help_text = self.command_parser.get_help_text()
        await self.send_response(update.effective_chat.id, help_text)

    async def _handle_new_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new command.

        Creates a new session for the user.

        Args:
            update: Telegram update object.
            context: Callback context.

        Requirements:
            - 11.5: Support basic commands (new)
            - 11.6: Parse and execute appropriate actions
        """
        user_id, telegram_user_id, username, _ = self._extract_telegram_identity(update)
        if await self._reject_if_not_whitelisted(
            telegram_user_id or "unknown", username, update.effective_chat.id
        ):
            return
        if not user_id:
            await self._reject_if_missing_username(update.effective_chat.id)
            return
        await self._execute_new_command(user_id, update.effective_chat.id)

    async def _handle_logs_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /logs command.

        Shows recent activity logs for the user.

        Args:
            update: Telegram update object.
            context: Callback context.

        Requirements:
            - 11.5: Support basic commands (logs)
            - 11.6: Parse and execute appropriate actions
        """
        user_id, telegram_user_id, username, _ = self._extract_telegram_identity(update)
        if await self._reject_if_not_whitelisted(
            telegram_user_id or "unknown", username, update.effective_chat.id
        ):
            return
        if not user_id:
            await self._reject_if_missing_username(update.effective_chat.id)
            return
        await self._execute_logs_command(user_id, update.effective_chat.id)

    async def _handle_skills_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /skills command - list all installed skills."""
        user_id, telegram_user_id, username, _ = self._extract_telegram_identity(update)
        chat_id = update.effective_chat.id

        if await self._reject_if_not_whitelisted(telegram_user_id or "unknown", username, chat_id):
            return

        if not user_id:
            await self._reject_if_missing_username(chat_id)
            return

        self._migrate_legacy_skill_folder(telegram_user_id, user_id)

        skills = self.skill_service.list_skills(user_id)

        if not skills:
            await self.send_response(chat_id, "No skills installed yet.")
            return

        skill_list = "\n".join(f"• {skill}" for skill in skills)
        await self.send_response(chat_id, f"📦 Installed skills:\n\n{skill_list}")

    async def _handle_add_skill_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /add_skill <url> command - install a skill from URL."""
        user_id, telegram_user_id, username, _ = self._extract_telegram_identity(update)
        chat_id = update.effective_chat.id

        if await self._reject_if_not_whitelisted(telegram_user_id or "unknown", username, chat_id):
            return

        if not user_id:
            await self._reject_if_missing_username(chat_id)
            return

        self._migrate_legacy_skill_folder(telegram_user_id, user_id)

        if not context.args:
            await self.send_response(
                chat_id,
                "Usage: /add_skill <url>\n\n"
                "Example: /add_skill https://github.com/user/repo/tree/main/skill",
            )
            return

        url = context.args[0]
        await self._execute_install_skill(user_id, chat_id, url)

    async def _handle_delete_skill_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /delete_skill <name> command - remove an installed skill."""
        user_id, telegram_user_id, username, _ = self._extract_telegram_identity(update)
        chat_id = update.effective_chat.id

        if await self._reject_if_not_whitelisted(telegram_user_id or "unknown", username, chat_id):
            return

        if not user_id:
            await self._reject_if_missing_username(chat_id)
            return

        self._migrate_legacy_skill_folder(telegram_user_id, user_id)

        if not context.args:
            await self.send_response(
                chat_id, "Usage: /delete_skill <name>\n\nUse /skills to see installed skills."
            )
            return

        skill_name = context.args[0]
        await self._execute_uninstall_skill(user_id, chat_id, skill_name)

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming text messages.

        Parses the message for commands and either executes them directly
        or forwards to the agent via SQS queue.

        Args:
            update: Telegram update object.
            context: Callback context.

        Requirements:
            - 11.2: Forward messages to Agent for processing
            - 11.4: Identify users by Telegram user ID
            - 11.6: Parse and execute appropriate actions
        """
        if not update.message or not update.message.text:
            return

        user_id, telegram_user_id, username, display_name = self._extract_telegram_identity(update)
        chat_id = update.effective_chat.id
        message_text = update.message.text

        if await self._reject_if_not_whitelisted(telegram_user_id or "unknown", username, chat_id):
            return

        if not user_id:
            await self._reject_if_missing_username(chat_id)
            return

        self._migrate_legacy_skill_folder(telegram_user_id, user_id)

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
        await self._execute_command(parsed, user_id, chat_id, message_text)

    async def _handle_document(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle incoming document attachments.

        Validates the document, downloads it, and enqueues a message
        with attachment metadata for agent processing.

        Args:
            update: Telegram update object.
            context: Callback context.

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

        user_id, telegram_user_id, username, _ = self._extract_telegram_identity(update)
        chat_id = update.effective_chat.id
        document = update.message.document
        caption = update.message.caption or ""

        if await self._reject_if_not_whitelisted(telegram_user_id or "unknown", username, chat_id):
            return

        if not user_id:
            await self._reject_if_missing_username(chat_id)
            return

        self._migrate_legacy_skill_folder(telegram_user_id, user_id)

        logger.info(
            "Received document from user %s: %s (%d bytes)",
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
            await self.send_response(chat_id, f"❌ {validation.error_message}")
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
            # Download and store file
            metadata = await self.file_service.download_file(
                bot=self.application.bot,
                file_id=document.file_id,
                user_id=user_id,
                file_name=validation.sanitized_name,
                mime_type=document.mime_type,
            )

            # Enqueue message with attachment
            await self._enqueue_message_with_attachments(
                user_id=user_id,
                chat_id=chat_id,
                message=caption,
                attachments=[metadata],
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
            await self.send_response(
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
            await self.send_response(
                chat_id,
                "❌ An error occurred processing your file.",
            )
            await self.logging_service.log_action(
                user_id=user_id,
                action="Document processing error",
                severity=LogSeverity.ERROR,
                details={"error": str(e)},
            )

    async def _handle_photo(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle incoming photo attachments.

        Selects the highest resolution photo variant, downloads it,
        and enqueues a message with attachment metadata.

        Args:
            update: Telegram update object.
            context: Callback context.

        Requirements:
            - 2.1: Download image attachments from Telegram
            - 2.3: Extract image metadata when available
            - 2.5: Download highest resolution version
            - 2.6: Forward image path and metadata to agent
            - 6.2: Add message handler for photo filter
        """
        if not update.message or not update.message.photo:
            return

        user_id, telegram_user_id, username, _ = self._extract_telegram_identity(update)
        chat_id = update.effective_chat.id
        caption = update.message.caption or ""

        if await self._reject_if_not_whitelisted(telegram_user_id or "unknown", username, chat_id):
            return

        if not user_id:
            await self._reject_if_missing_username(chat_id)
            return

        self._migrate_legacy_skill_folder(telegram_user_id, user_id)

        # Select highest resolution photo (Requirement 2.5)
        # Photos are sorted by size, last one is largest
        photo = self._select_highest_resolution_photo(update.message.photo)

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
            await self.send_response(
                chat_id,
                f"❌ Photo too large. Maximum size is {self.config.max_file_size_mb}MB",
            )
            return

        try:
            # Generate filename for photo
            file_name = f"photo_{photo.file_unique_id}.jpg"

            # Download and store file
            metadata = await self.file_service.download_file(
                bot=self.application.bot,
                file_id=photo.file_id,
                user_id=user_id,
                file_name=file_name,
                mime_type="image/jpeg",
            )

            # Enqueue message with attachment
            await self._enqueue_message_with_attachments(
                user_id=user_id,
                chat_id=chat_id,
                message=caption,
                attachments=[metadata],
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
            await self.send_response(
                chat_id,
                "❌ Failed to download photo. Please try again.",
            )
        except Exception as e:
            logger.error("Photo handling error: %s", e)
            await self.send_response(
                chat_id,
                "❌ An error occurred processing your photo.",
            )

    def _select_highest_resolution_photo(
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
        return max(photos, key=lambda p: p.file_size)

    async def _execute_command(
        self,
        parsed: ParsedCommand,
        user_id: str,
        chat_id: int,
        original_message: str,
    ) -> None:
        """Execute a parsed command.

        Routes the command to the appropriate handler based on type.

        Args:
            parsed: Parsed command with type and arguments.
            user_id: Telegram user ID.
            chat_id: Telegram chat ID for responses.
            original_message: Original message text.

        Requirements:
            - 11.5: Support basic commands
            - 11.6: Parse and execute appropriate actions
        """
        match parsed.command_type:
            case CommandType.NEW:
                await self._execute_new_command(user_id, chat_id)

            case CommandType.LOGS:
                await self._execute_logs_command(user_id, chat_id)

            case CommandType.HELP:
                help_text = self.command_parser.get_help_text()
                await self.send_response(chat_id, help_text)

            case CommandType.INSTALL_SKILL:
                if parsed.args:
                    await self._execute_install_skill(user_id, chat_id, parsed.args[0])
                else:
                    await self.send_response(
                        chat_id,
                        "Please provide a URL: install skill <url>",
                    )

            case CommandType.UNINSTALL_SKILL:
                if parsed.args:
                    await self._execute_uninstall_skill(user_id, chat_id, parsed.args[0])
                else:
                    await self.send_response(
                        chat_id,
                        "Please provide a skill name: uninstall skill <name>",
                    )

            case CommandType.MESSAGE:
                # Forward to agent via SQS queue
                await self._enqueue_message(user_id, chat_id, original_message)

    async def _execute_new_command(self, user_id: str, chat_id: int) -> None:
        """Execute the 'new' command to create a fresh session.

        Triggers memory extraction before clearing the session to preserve
        important information in long-term memory.

        Args:
            user_id: Telegram user ID.
            chat_id: Telegram chat ID for responses.

        Requirements:
            - 11.5: Support basic commands (new)
            - 4.1: Invoke MemoryExtractionService before clearing
            - 4.4: Inform user that conversation was analyzed
        """
        logger.info("Creating new session for user %s", user_id)

        # Create new session via agent service (triggers extraction)
        _, notification = await self.agent_service.new_session(user_id)

        await self.send_response(chat_id, notification)

        # Log the action
        await self.logging_service.log_action(
            user_id=user_id,
            action="Started new session",
            severity=LogSeverity.INFO,
        )

    async def _execute_logs_command(self, user_id: str, chat_id: int) -> None:
        """Execute the 'logs' command to show recent activity.

        Args:
            user_id: Telegram user ID.
            chat_id: Telegram chat ID for responses.

        Requirements:
            - 11.5: Support basic commands (logs)
        """
        logger.debug("Fetching logs for user %s", user_id)

        logs = await self.logging_service.get_recent_logs(user_id, hours=24)

        if not logs:
            await self.send_response(chat_id, "No recent activity logs found.")
            return

        # Format logs for display
        log_lines = ["📋 Recent Activity (last 24 hours):\n"]
        for log_entry in logs[:20]:  # Limit to 20 entries
            timestamp = log_entry.timestamp.strftime("%H:%M:%S")
            severity_emoji = self._get_severity_emoji(log_entry.severity)
            log_lines.append(f"{severity_emoji} [{timestamp}] {log_entry.action}")

        await self.send_response(chat_id, "\n".join(log_lines))

    def _get_severity_emoji(self, severity: LogSeverity) -> str:
        """Get emoji for log severity level.

        Args:
            severity: Log severity level.

        Returns:
            Emoji string for the severity.
        """
        match severity:
            case LogSeverity.DEBUG:
                return "🔍"
            case LogSeverity.INFO:
                return "ℹ️"
            case LogSeverity.WARNING:
                return "⚠️"
            case LogSeverity.ERROR:
                return "❌"
            case _:
                return "📝"

    async def _execute_install_skill(self, user_id: str, chat_id: int, url: str) -> None:
        """Execute the 'install skill' command.

        Args:
            user_id: Telegram user ID.
            chat_id: Telegram chat ID for responses.
            url: URL to download the skill from.

        Requirements:
            - 11.5: Support basic commands (install skill)
        """
        logger.info("Downloading skill from %s to pending for user %s", url, user_id)

        await self.send_response(chat_id, f"⏳ Downloading skill to pending/ from {url}...")

        try:
            res = self.skill_service.download_skill_to_pending(url, user_id, scope="user")
            metadata = res.get("metadata")
            await self.send_response(
                chat_id,
                (
                    f"✅ Skill '{metadata.name}' downloaded to pending successfully!\n"
                    f"Pending path: {res.get('pending_dir')}\n\n"
                    "Next:\n"
                    "1) Review the pending skill's SKILL.md and scripts\n"
                    "2) Run onboarding (AI review required)\n"
                    '   - onboard_pending_skills(scope="user", ai_review_completed=true)\n'
                ),
            )

            # Log the action
            await self.logging_service.log_action(
                user_id=user_id,
                action=f"Downloaded skill to pending: {metadata.name}",
                severity=LogSeverity.INFO,
                details={
                    "url": url,
                    "skill_name": metadata.name,
                    "pending_dir": res.get("pending_dir"),
                },
            )

        except SkillInstallError as e:
            error_msg = f"❌ Failed to install skill: {str(e)}"
            await self.send_response(chat_id, error_msg)

            # Log the error
            await self.logging_service.log_action(
                user_id=user_id,
                action=f"Failed to install skill from {url}",
                severity=LogSeverity.ERROR,
                details={"url": url, "error": str(e)},
            )

    async def _execute_uninstall_skill(self, user_id: str, chat_id: int, skill_name: str) -> None:
        """Execute the 'uninstall skill' command.

        Args:
            user_id: Telegram user ID.
            chat_id: Telegram chat ID for responses.
            skill_name: Name of the skill to uninstall.

        Requirements:
            - 11.5: Support basic commands (uninstall skill)
        """
        logger.info("Uninstalling skill %s for user %s", skill_name, user_id)

        try:
            result = self.skill_service.uninstall_skill(skill_name, user_id)
            await self.send_response(chat_id, f"✅ {result}")

            # Log the action
            await self.logging_service.log_action(
                user_id=user_id,
                action=f"Uninstalled skill: {skill_name}",
                severity=LogSeverity.INFO,
                details={"skill_name": skill_name},
            )

        except SkillNotFoundError as e:
            await self.send_response(chat_id, f"❌ {str(e)}")

            # Log the error
            await self.logging_service.log_action(
                user_id=user_id,
                action=f"Failed to uninstall skill: {skill_name}",
                severity=LogSeverity.WARNING,
                details={"skill_name": skill_name, "error": str(e)},
            )

    async def _enqueue_message(self, user_id: str, chat_id: int, message: str) -> None:
        """Enqueue a message to the user's SQS queue for processing.

        Args:
            user_id: Telegram user ID.
            chat_id: Telegram chat ID for responses.
            message: Message text to process.

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

        # Send to SQS queue
        self.sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(payload),
        )

        logger.info("Message enqueued for user %s to queue %s", user_id, queue_url)

        # Log the action
        await self.logging_service.log_action(
            user_id=user_id,
            action="Message enqueued for processing",
            severity=LogSeverity.DEBUG,
            details={"message_preview": message[:50]},
        )

    async def _enqueue_message_with_attachments(
        self,
        user_id: str,
        chat_id: int,
        message: str,
        attachments: list,
    ) -> None:
        """Enqueue a message with file attachments to SQS queue.

        Args:
            user_id: Telegram user ID.
            chat_id: Telegram chat ID for responses.
            message: Message text (caption) to process.
            attachments: List of FileMetadata objects.

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

        # Log the action
        await self.logging_service.log_action(
            user_id=user_id,
            action=f"Message with {len(attachments)} attachment(s) enqueued",
            severity=LogSeverity.DEBUG,
            details={
                "message_preview": message[:50] if message else "(no text)",
                "attachment_count": len(attachments),
            },
        )

    async def send_response(self, chat_id: int, response: str) -> None:
        """Send a response message to a Telegram chat.

        Args:
            chat_id: Telegram chat ID to send to.
            response: Response text to send.

        Requirements:
            - 11.3: Send agent responses back to user
        """
        from telegram.constants import ParseMode

        # Convert markdown to Telegram HTML format (more reliable than MarkdownV2)
        formatted = self._format_for_telegram_html(response)

        try:
            # Split long messages (Telegram has 4096 char limit)
            max_length = 4096
            if len(formatted) <= max_length:
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=formatted,
                    parse_mode=ParseMode.HTML,
                )
            else:
                # Split into chunks
                for i in range(0, len(formatted), max_length):
                    chunk = formatted[i : i + max_length]
                    await self.application.bot.send_message(
                        chat_id=chat_id,
                        text=chunk,
                        parse_mode=ParseMode.HTML,
                    )

            logger.debug("Response sent to chat %s", chat_id)

        except Exception as e:
            logger.error("Failed to send response to chat %s: %s", chat_id, e)
            # Fallback to plain text if markdown parsing fails
            try:
                await self.application.bot.send_message(chat_id=chat_id, text=response)
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
                    await self.application.bot.send_document(
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
                    await self.application.bot.send_photo(
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

    def _format_for_telegram_html(self, text: str) -> str:
        """Convert standard markdown to Telegram HTML format.

        HTML is more reliable than MarkdownV2 for complex formatting like tables.

        Args:
            text: Text with standard markdown.

        Returns:
            Telegram HTML formatted text.
        """
        import re
        from html import escape

        # Extract code blocks BEFORE escaping (preserve their content)
        code_blocks = []

        def save_code_block(match: re.Match) -> str:
            code_blocks.append(match.group(1))
            return f"__CODE_BLOCK_{len(code_blocks) - 1}__"

        result = re.sub(r"```(?:\w+)?\n?(.*?)```", save_code_block, text, flags=re.DOTALL)

        # Now escape HTML special characters
        result = escape(result)

        # Convert markdown tables into a human-friendly list.
        # Telegram does not render markdown pipe tables reliably.
        def convert_table(match: re.Match) -> str:
            raw_lines = [ln.strip() for ln in match.group(0).strip().split("\n") if ln.strip()]
            if len(raw_lines) < 2:
                return match.group(0)

            # Drop separator lines (contains only |, -, :, and spaces)
            lines = [ln for ln in raw_lines if not re.match(r"^[\|\-\s:]+$", ln)]
            if not lines:
                return ""

            # Parse header + rows
            header = [c.strip() for c in lines[0].strip("|").split("|")]
            rows = [[c.strip() for c in ln.strip("|").split("|")] for ln in lines[1:]]

            out_lines: list[str] = []
            for idx, row in enumerate(rows, start=1):
                # Pad/truncate to header length
                if len(row) < len(header):
                    row = row + [""] * (len(header) - len(row))
                if len(row) > len(header) and header:
                    row = row[: len(header)]

                if header and any(h for h in header):
                    parts: list[str] = []
                    for h, v in zip(header, row, strict=False):
                        h = (h or "").strip()
                        v = (v or "").strip()
                        if not h and not v:
                            continue
                        if h and v:
                            parts.append(f"{h}: {v}")
                        elif v:
                            parts.append(v)
                    line = f"{idx}. " + "; ".join(parts)
                else:
                    # Fallback: no header found
                    parts = [c for c in row if c]
                    line = f"{idx}. " + " ".join(parts)

                out_lines.append(line.strip())

            return "\n".join(out_lines)

        # Match markdown tables (lines starting with |)
        result = re.sub(r"(?:^\|.+\|$\n?)+", convert_table, result, flags=re.MULTILINE)

        # Convert headers to bold
        result = re.sub(r"^### (.+)$", r"<b>\1</b>", result, flags=re.MULTILINE)
        result = re.sub(r"^## (.+)$", r"<b>\1</b>", result, flags=re.MULTILINE)
        result = re.sub(r"^# (.+)$", r"<b>\1</b>", result, flags=re.MULTILINE)

        # Convert **bold** to <b>bold</b>
        result = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", result)

        # Convert *italic* to <i>italic</i>
        result = re.sub(r"\*(.+?)\*", r"<i>\1</i>", result)

        # Convert `code` to <code>code</code>
        result = re.sub(r"`([^`]+)`", r"<code>\1</code>", result)

        # Restore code blocks as <pre> (escape their content now)
        for i, code in enumerate(code_blocks):
            result = result.replace(f"__CODE_BLOCK_{i}__", f"<pre>{escape(code)}</pre>")

        # Convert [text](url) to <a href="url">text</a>
        result = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', result)

        return result

    def _format_for_telegram(self, text: str) -> str:
        """Convert standard markdown to Telegram MarkdownV2 format.

        Telegram MarkdownV2 requires escaping special characters.

        Args:
            text: Text with standard markdown.

        Returns:
            Telegram MarkdownV2 formatted text.
        """
        import re

        # Characters that need escaping in MarkdownV2
        # (except those used for formatting: * _ ` [ ])
        special_chars = r"([.!#()\-+={}|>])"

        # First, escape special characters
        result = re.sub(special_chars, r"\\\1", text)

        # Convert # headers to bold (Telegram doesn't support headers)
        result = re.sub(r"^\\#\\#\\# (.+)$", r"*\1*", result, flags=re.MULTILINE)
        result = re.sub(r"^\\#\\# (.+)$", r"*\1*", result, flags=re.MULTILINE)
        result = re.sub(r"^\\# (.+)$", r"*\1*", result, flags=re.MULTILINE)

        return result

    async def start(self) -> None:
        """Start the Telegram bot.

        Initializes the bot and starts polling for updates.

        Requirements:
            - 11.1: Connect to Telegram Bot API using configured bot token
        """
        logger.info("Starting Telegram bot...")

        # Initialize the application
        await self.application.initialize()
        await self.application.start()

        # Start polling for updates
        await self.application.updater.start_polling(allowed_updates=Update.ALL_TYPES)

        logger.info("Telegram bot started and polling for updates")

    async def stop(self) -> None:
        """Stop the Telegram bot.

        Gracefully shuts down the bot and stops polling.
        """
        logger.info("Stopping Telegram bot...")

        if self.application.updater.running:
            await self.application.updater.stop()

        await self.application.stop()
        await self.application.shutdown()

        logger.info("Telegram bot stopped")

    def run_polling(self) -> None:
        """Run the bot with polling (blocking).

        This is a convenience method for running the bot in standalone mode.
        For integration with other async components, use start() and stop().
        """
        logger.info("Running Telegram bot with polling...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

    def get_bot_info(self) -> dict:
        """Get information about the bot.

        Returns:
            Dictionary with bot information.
        """
        bot = self.application.bot
        return {
            "id": bot.id,
            "username": bot.username,
            "first_name": bot.first_name,
        }
