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

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import AgentConfig
from app.dao import UserDAO
from app.enums import LogSeverity
from app.services.command_parser import CommandParser
from app.services.file_service import FileService
from app.services.logging_service import LoggingService
from app.services.onboarding_service import OnboardingService
from app.services.skill_service import SkillService

# Telegram helper modules
from app.telegram.command_executor import CommandExecutor
from app.telegram.message_handlers import TelegramMessageHandlers
from app.telegram.message_queue import MessageQueueHandler
from app.telegram.response_formatter import TelegramResponseFormatter

try:
    from mypy_boto3_sqs import SQSClient  # type: ignore[reportMissingImports]
except Exception:  # pragma: no cover

    class SQSClient(Protocol):
        def send_message(self, **kwargs: Any) -> Any: ...


if TYPE_CHECKING:
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
        sqs_client: SQSClient,
        queue_manager: SQSQueueManager,
        agent_service: AgentService,
        logging_service: LoggingService,
        skill_service: SkillService,
        file_service: FileService | None = None,
        command_parser: CommandParser | None = None,
        user_dao: UserDAO | None = None,
        onboarding_service: OnboardingService | None = None,
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
            user_dao: User DAO for user management operations.
            onboarding_service: Service for handling user onboarding.
        """
        self.config = config
        self.agent_service = agent_service
        self.logging_service = logging_service
        self.skill_service = skill_service
        self.file_service = file_service or FileService(config)
        self.command_parser = command_parser or CommandParser()
        self.user_dao = user_dao
        self.onboarding_service = onboarding_service

        # Hot-reloading whitelist: reads allowed_users from secrets.yml on demand.
        from app.security.whitelist import live_allowed_users

        self._get_allowed_users_live = live_allowed_users(config.secrets_path)

        # Build the application with the bot token
        self.application = Application.builder().token(config.telegram_bot_token).build()

        # Initialize helper modules
        self._formatter = TelegramResponseFormatter()
        self._queue_handler = MessageQueueHandler(sqs_client, queue_manager)

        # Initialize command executor
        self._command_executor = CommandExecutor(
            agent_service=agent_service,
            skill_service=skill_service,
            logging_service=logging_service,
            command_parser=self.command_parser,
            enqueue_callback=self._enqueue_message,
            send_response_callback=self._send_response_via_sender,
        )

        # Initialize message handlers
        self._message_handlers = TelegramMessageHandlers(
            config=config,
            logging_service=logging_service,
            skill_service=skill_service,
            file_service=self.file_service,
            command_parser=self.command_parser,
            bot_application=self.application,
            get_allowed_users=self._get_allowed_users_live,
            user_dao=user_dao,
            onboarding_service=onboarding_service,
        )

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
        self.application.add_handler(CommandHandler("cancel", self._handle_cancel_command))
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
            # Voice notes and audio files are separate message types in Telegram.
            self.application.add_handler(MessageHandler(filters.VOICE, self._handle_voice))
            self.application.add_handler(MessageHandler(filters.AUDIO, self._handle_audio))

        # General message handler for all text messages
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )

        logger.debug("Telegram handlers registered")

    # ========================================================================
    # Handler Wrappers (delegate to message_handlers module)
    # ========================================================================

    async def _handle_start_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._message_handlers.handle_start_command(update, context)

    async def _handle_help_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._message_handlers.handle_help_command(update, context)

    async def _handle_new_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._message_handlers.handle_new_command(
            update, context, execute_new=self._command_executor.execute_new_command
        )

    async def _handle_cancel_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._message_handlers.handle_cancel_command(
            update, context, execute_cancel=self._command_executor.execute_cancel_command
        )

    async def _handle_logs_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._message_handlers.handle_logs_command(
            update, context, execute_logs=self._command_executor.execute_logs_command
        )

    async def _handle_skills_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._message_handlers.handle_skills_command(update, context)

    async def _handle_add_skill_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._message_handlers.handle_add_skill_command(
            update, context, execute_install=self._command_executor.execute_install_skill
        )

    async def _handle_delete_skill_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._message_handlers.handle_delete_skill_command(
            update, context, execute_uninstall=self._command_executor.execute_uninstall_skill
        )

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._message_handlers.handle_message(
            update, context, execute_command=self._command_executor.execute_command
        )

    async def _handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._message_handlers.handle_document(
            update, context, enqueue_with_attachments=self._enqueue_message_with_attachments
        )

    async def _handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._message_handlers.handle_photo(
            update, context, enqueue_with_attachments=self._enqueue_message_with_attachments
        )

    async def _handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._message_handlers.handle_voice(
            update, context, enqueue_with_attachments=self._enqueue_message_with_attachments
        )

    async def _handle_audio(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._message_handlers.handle_audio(
            update, context, enqueue_with_attachments=self._enqueue_message_with_attachments
        )

    # ========================================================================
    # Queue Operations (delegate to message_queue module)
    # ========================================================================

    async def _enqueue_message(
        self,
        user_id: str,
        chat_id: int,
        message: str,
        onboarding_context: dict[str, str | None] | None = None,
    ) -> None:
        """Enqueue a message to the user's SQS queue for processing.

        Args:
            user_id: Telegram user ID.
            chat_id: Telegram chat ID for responses.
            message: Message text to process.
            onboarding_context: Optional onboarding context (soul.md, id.md content)
                if this is the user's first interaction.
        """
        self._queue_handler.enqueue_message(user_id, chat_id, message, onboarding_context)

        # Log the action
        try:
            await self.logging_service.log_action(
                user_id=user_id,
                action="Message enqueued for processing",
                severity=LogSeverity.DEBUG,
                details={"message_preview": message[:50]},
            )
        except Exception:
            logger.exception("Failed to log enqueue action")

    async def _enqueue_message_with_attachments(
        self,
        user_id: str,
        chat_id: int,
        message: str,
        attachments: list[Any],
    ) -> None:
        """Enqueue a message with file attachments to SQS queue."""
        self._queue_handler.enqueue_message_with_attachments(user_id, chat_id, message, attachments)

        # Log the action
        try:
            await self.logging_service.log_action(
                user_id=user_id,
                action=f"Message with {len(attachments)} attachment(s) enqueued",
                severity=LogSeverity.DEBUG,
                details={
                    "message_preview": message[:50] if message else "(no text)",
                    "attachment_count": len(attachments),
                },
            )
        except Exception:
            logger.exception("Failed to log enqueue with attachments action")

    # ========================================================================
    # Response/Sending Methods (delegate to message_sender module)
    # ========================================================================

    async def _send_response_via_sender(self, chat_id: int, response: str) -> None:
        """Send a response using the message sender module."""
        from app.telegram.message_sender import TelegramMessageSender

        sender = TelegramMessageSender(self.application.bot)
        await sender.send_response(chat_id, response)

    async def send_response(self, chat_id: int, response: str) -> None:
        """Send a response message to a Telegram chat.

        Args:
            chat_id: Telegram chat ID to send to.
            response: Response text to send.

        Requirements:
            - 11.3: Send agent responses back to user
        """
        await self._send_response_via_sender(chat_id, response)

    async def send_file(
        self,
        chat_id: int,
        file_path: str | Path,
        caption: str | None = None,
    ) -> bool:
        """Send a file to a Telegram chat.

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
        from app.telegram.message_sender import TelegramMessageSender

        sender = TelegramMessageSender(self.application.bot)
        return await sender.send_file(chat_id, file_path, caption)

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
        from app.telegram.message_sender import TelegramMessageSender

        sender = TelegramMessageSender(self.application.bot)
        return await sender.send_photo(chat_id, photo_path, caption)

    async def send_progress(self, chat_id: int, message: str) -> bool:
        """Send a short progress update message to a Telegram chat.

        Used during long-running operations to keep the user informed.

        Args:
            chat_id: Telegram chat ID to send to.
            message: Progress message to send.

        Returns:
            True if send succeeded, False otherwise.
        """
        from app.telegram.message_sender import TelegramMessageSender

        sender = TelegramMessageSender(self.application.bot)

        # Truncate very long messages to avoid spam
        MAX_LENGTH = 200
        if len(message) > MAX_LENGTH:
            message = message[: MAX_LENGTH - 3] + "..."

        try:
            await sender.send_response(chat_id, message)
            return True
        except Exception as e:
            logger.warning("Failed to send progress to chat %s: %s", chat_id, e)
            return False

    # ========================================================================
    # Deprecated Formatting Methods (kept for compatibility, delegate to formatter)
    # ========================================================================

    def _format_for_telegram_html(self, text: str) -> str:
        """Convert standard markdown to Telegram HTML format. DEPRECATED - Use formatter module."""
        return self._formatter.format_for_html(text)

    def _format_for_telegram(self, text: str) -> str:
        """Convert standard markdown to Telegram MarkdownV2 format. DEPRECATED - Use formatter module."""
        return self._formatter.format_for_markdown_v2(text)

    def _get_severity_emoji(self, severity: Any) -> str:
        """Get emoji for log severity level. DEPRECATED - Use formatter module."""
        return self._formatter.get_severity_emoji(severity)

    # ========================================================================
    # Bot Lifecycle Methods
    # ========================================================================

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
        updater = self.application.updater
        if updater is None:
            logger.warning("Telegram Application.updater is None; cannot start polling")
            return
        await updater.start_polling(allowed_updates=Update.ALL_TYPES)

        logger.info("Telegram bot started and polling for updates")

    async def stop(self) -> None:
        """Stop the Telegram bot.

        Gracefully shuts down the bot and stops polling.
        """
        logger.info("Stopping Telegram bot...")

        updater = self.application.updater
        if updater is not None and updater.running:
            await updater.stop()

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
