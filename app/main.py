"""Application entry point and bootstrap.

This module initializes all application components, wires dependencies,
and provides the main entry point for running the application.

Requirements:
- All integration: Initialize Database, DAOs, Services, Routers
- 13.1, 13.2, 13.3: Setup FastAPI app with task endpoints
- System reliability: Graceful shutdown handling
"""

import asyncio
import logging
import os
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import boto3
from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler

# Bypass tool consent prompts for automated execution
os.environ["BYPASS_TOOL_CONSENT"] = "true"

from app.config import AgentConfig
from app.dao import LogDAO, TaskDAO, UserDAO
from app.dao.conversation_dao import ConversationDAO
from app.dao.cron_dao import CronDAO
from app.dao.cron_lock_dao import CronLockDAO
from app.database import Database
from app.routers import create_task_router, create_webhook_router
from app.scheduler.cron_scheduler import CronScheduler
from app.scheduler.system_scheduler import SystemScheduler
from app.services.file_service import FileService
from app.services import (
    CommandParser,
    LoggingService,
    PendingSkillService,
    SkillService,
    TaskService,
    WebhookService,
)
from app.services.agent_service import AgentService
from app.services.cron_service import CronService
from app.services.memory_service import MemoryService
from app.services.onboarding_service import OnboardingService
from app.sqs.message_processor import MessageProcessor
from app.sqs.queue_manager import SQSQueueManager
from app.telegram.bot import TelegramBotInterface
from app.logging_filters import install_uvicorn_access_log_filters
from app.observability.forbidden_access_log import log_forbidden_request
from app.security.whitelist import live_allowed_users
from app.observability.health_state import (
    snapshot as health_snapshot,
    start_stall_watchdog,
)
from app.observability.error_log_file import setup_error_log_file

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# Suppress verbose HTTP request logs from telegram bot
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

# Filter to suppress spurious warnings from MCP client library
# The session.py module uses logging.warning() directly (root logger),
# so we need a custom filter to suppress specific messages
class McpWarningFilter(logging.Filter):
    """Filter out known benign warnings from MCP library."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Filter out SSE keepalive warnings
        if "Unknown SSE event: keepalive" in record.getMessage():
            return False
        # Filter out notifications/initialized validation errors
        # (known issue: InitializedNotification not in ServerNotification union)
        if "Failed to validate notification" in record.getMessage():
            if "notifications/initialized" in record.getMessage():
                return False
        return True


# Apply the filter to the root logger's handler
for handler in logging.root.handlers:
    handler.addFilter(McpWarningFilter())


class Application:
    """Main application container.

    Manages all application components and their lifecycle.
    Provides dependency injection and graceful shutdown.
    """

    def __init__(self, config: AgentConfig) -> None:
        """Initialize the application with configuration.

        Args:
            config: Application configuration.
        """
        self.config = config
        self._shutdown_event = asyncio.Event()

        # Core components (initialized in setup)
        self.database: Database | None = None
        self.sqs_client = None
        self.fastapi_app: FastAPI | None = None

        # DAOs
        self.user_dao: UserDAO | None = None
        self.task_dao: TaskDAO | None = None
        self.log_dao: LogDAO | None = None
        self.cron_dao: CronDAO | None = None
        self.cron_lock_dao: CronLockDAO | None = None
        self.conversation_dao: ConversationDAO | None = None

        # Services
        self.agent_service: AgentService | None = None
        self.memory_service: MemoryService | None = None
        self.task_service: TaskService | None = None
        self.logging_service: LoggingService | None = None
        self.skill_service: SkillService | None = None
        self.pending_skill_service: PendingSkillService | None = None
        self.webhook_service: WebhookService | None = None
        self.command_parser: CommandParser | None = None
        self.cron_service: CronService | None = None

        # SQS components
        self.queue_manager: SQSQueueManager | None = None
        self.message_processor: MessageProcessor | None = None

        # Telegram bot
        self.telegram_bot: TelegramBotInterface | None = None

        # Cron scheduler
        self.cron_scheduler: CronScheduler | None = None

        # System scheduler (file cleanup)
        self.system_scheduler: SystemScheduler | None = None

        # File service
        self.file_service: FileService | None = None

        # Background tasks
        self._background_tasks: list[asyncio.Task] = []

    def _create_sqs_client(self):
        """Create SQS client (LocalStack for local dev).

        Returns:
            Boto3 SQS client configured for LocalStack or AWS.
        """
        if self.config.localstack_endpoint:
            logger.info("Using LocalStack SQS at %s", self.config.localstack_endpoint)
            return boto3.client(
                "sqs",
                endpoint_url=self.config.localstack_endpoint,
                region_name=self.config.aws_region,
                aws_access_key_id="test",
                aws_secret_access_key="test",
            )
        else:
            logger.info("Using AWS SQS in region %s", self.config.aws_region)
            return boto3.client("sqs", region_name=self.config.aws_region)

    async def setup(self) -> None:
        """Initialize all application components.

        Sets up database, DAOs, services, and routers with proper
        dependency injection.

        Requirements:
            - All integration: Initialize Database, DAOs, Services, Routers
        """
        logger.info("Setting up application components...")

        # Initialize error log file handler early to capture setup errors
        setup_error_log_file(self.config)

        # Initialize database
        self.database = Database(self.config.database_url)
        if getattr(self.config, "auto_create_tables", False):
            await self.database.init_db()
            logger.info("Database initialized (auto_create_tables=true)")
        else:
            logger.info(
                "Database initialized (auto_create_tables=false; relying on Alembic migrations)"
            )

        # Initialize DAOs
        self.user_dao = UserDAO(self.database)
        self.task_dao = TaskDAO(self.database)
        self.log_dao = LogDAO(self.database)
        self.cron_dao = CronDAO(self.database)
        self.cron_lock_dao = CronLockDAO(self.database)
        self.conversation_dao = ConversationDAO(self.database.session)
        logger.info("DAOs initialized")

        # Initialize services
        self.command_parser = CommandParser()
        self.logging_service = LoggingService(self.log_dao)
        self.skill_service = SkillService(self.config)
        self.onboarding_service = OnboardingService(
            vault_root=self.config.obsidian_vault_root,
        )

        # Pending skills: create service and run preflight (bounded, non-fatal)
        self.pending_skill_service = PendingSkillService(self.config)
        if self.config.pending_skills_preflight_enabled:
            try:
                summary = self.pending_skill_service.preflight_all()
                logger.info(
                    "Pending skill preflight: processed=%s failures=%s",
                    summary.get("processed"),
                    summary.get("failures"),
                )
            except Exception as e:
                logger.warning(
                    "Pending skill preflight failed (continuing startup): %s",
                    e,
                )

        # Initialize memory service if memory is enabled
        self.memory_service = None
        if self.config.memory_enabled:
            try:
                self.memory_service = MemoryService(self.config)
                logger.info("Memory service initialized")
            except Exception as e:
                logger.warning(
                    "Failed to initialize memory service: %s. Agent will operate without memory.", e
                )

        self.agent_service = AgentService(
            self.config,
            self.memory_service,
            pending_skill_service=self.pending_skill_service,
            skill_service=self.skill_service,
            logging_service=self.logging_service,
            conversation_dao=self.conversation_dao,
        )
        self.task_service = TaskService(self.task_dao, self.user_dao, self.log_dao)
        self.webhook_service = WebhookService(self.logging_service)
        logger.info("Services initialized")

        # Initialize SQS components
        self.sqs_client = self._create_sqs_client()
        self.queue_manager = SQSQueueManager(self.sqs_client, self.config.sqs_queue_prefix)
        logger.info("SQS components initialized")

        # Initialize message processor with response callback
        self.message_processor = MessageProcessor(
            sqs_client=self.sqs_client,
            queue_manager=self.queue_manager,
            agent_service=self.agent_service,
            config=self.config,
            user_dao=self.user_dao,
            response_callback=self._send_telegram_response,
            file_send_callback=self._send_telegram_file,
            progress_callback=self._send_telegram_progress,
            typing_action_callback=self._send_telegram_typing_action,
        )
        logger.info("Message processor initialized")

        # Initialize Telegram bot
        self.telegram_bot = TelegramBotInterface(
            config=self.config,
            sqs_client=self.sqs_client,
            queue_manager=self.queue_manager,
            agent_service=self.agent_service,
            logging_service=self.logging_service,
            skill_service=self.skill_service,
            command_parser=self.command_parser,
            user_dao=self.user_dao,
            onboarding_service=self.onboarding_service,
        )
        logger.info("Telegram bot initialized")

        # Initialize cron service and scheduler
        self.cron_service = CronService(
            cron_dao=self.cron_dao,
            lock_dao=self.cron_lock_dao,
            agent_service=self.agent_service,
            telegram_bot=self.telegram_bot,
            logging_service=self.logging_service,
        )
        self.cron_scheduler = CronScheduler(
            cron_service=self.cron_service,
            lock_dao=self.cron_lock_dao,
            agent_service=self.agent_service,
            user_dao=self.user_dao,
            telegram_bot=self.telegram_bot,
            logging_service=self.logging_service,
        )

        # INTERNAL SYSTEM CRON (non-user-editable): consolidate per-user Obsidian
        # short-term memories into long-term memory daily at 00:01.
        # This is registered as a *system task* (in-memory) and is not stored
        # in the DB, therefore users (and agent tools) cannot modify it.
        self.cron_scheduler.register_system_task(
            name="daily-short-term-memory-consolidation",
            cron_expression="1 0 * * *",
            callback=self.agent_service.consolidate_short_term_memories_daily,
        )
        logger.info("Cron service and scheduler initialized")

        # Wire cron service to agent service for cron tools + prompt injection.
        if self.agent_service is not None:
            self.agent_service.set_cron_service(self.cron_service)
        logger.info("Cron service wired to agent service")

        # Initialize file service and system scheduler for file cleanup
        self.file_service = FileService(self.config)
        self.system_scheduler = SystemScheduler(
            config=self.config,
            file_service=self.file_service,
        )
        logger.info("File service and system scheduler initialized")

        logger.info("Application setup complete")

    async def _send_telegram_response(self, chat_id: int, response: str) -> None:
        """Send response via Telegram bot.

        Callback for message processor to send agent responses.

        Args:
            chat_id: Telegram chat ID.
            response: Response text to send.
        """
        if self.telegram_bot:
            await self.telegram_bot.send_response(chat_id, response)

    async def _send_telegram_file(
        self, chat_id: int, file_path: str | Path, caption: str | None = None
    ) -> bool:
        """Send file via Telegram bot.

        Callback for message processor to send files to users.
        Routes to send_photo for images, send_file for other documents.

        Args:
            chat_id: Telegram chat ID.
            file_path: Path to the file to send.
            caption: Optional caption for the file.

        Returns:
            True if send succeeded, False otherwise.
        """
        if not self.telegram_bot:
            return False

        file_path = Path(file_path)
        # Check if it's an image that should be sent as photo
        image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
        if file_path.suffix.lower() in image_extensions:
            return await self.telegram_bot.send_photo(chat_id, file_path, caption)
        return await self.telegram_bot.send_file(chat_id, file_path, caption)

    async def _send_telegram_progress(self, chat_id: int, message: str) -> bool:
        """Send progress update via Telegram bot.

        Callback for message processor to send progress updates.

        Args:
            chat_id: Telegram chat ID.
            message: Progress message to send.

        Returns:
            True if send succeeded, False otherwise.
        """
        if self.telegram_bot:
            return await self.telegram_bot.send_progress(chat_id, message)
        return False

    async def _send_telegram_typing_action(self, chat_id: int, action: str) -> None:
        """Send typing action via Telegram bot.

        Callback for message processor to send chat actions (typing indicator).

        Args:
            chat_id: Telegram chat ID.
            action: Chat action type (typing, upload_document, etc.).
        """
        if self.telegram_bot and self.telegram_bot.application.bot:
            from app.telegram.message_sender import TelegramMessageSender

            sender = TelegramMessageSender(self.telegram_bot.application.bot)
            await sender.send_chat_action(chat_id, action)
        else:
            logger.debug(
                "Telegram bot not initialized, skipping typing action for chat %s",
                chat_id,
            )

    def create_fastapi_app(self) -> FastAPI:
        """Create and configure FastAPI application.

        Sets up routers and lifespan management.

        Returns:
            Configured FastAPI application.

        Requirements:
            - 13.1, 13.2, 13.3: Setup FastAPI app with task endpoints
        """

        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
            """Manage application lifespan."""
            logger.info("FastAPI application starting...")
            yield
            logger.info("FastAPI application shutting down...")

        self.fastapi_app = FastAPI(
            title="Mordecai",
            description="Mordecai — multi-user AI agent platform with Telegram",
            version="1.0.0",
            lifespan=lifespan,
        )

        # Capture request data early so we can log it on errors (e.g., 403) without
        # consuming the body for downstream handlers.
        @self.fastapi_app.middleware("http")
        async def _capture_request_for_error_logs(request: Request, call_next):
            max_bytes = 32 * 1024
            try:
                content_type = request.headers.get("content-type", "")
                # Avoid eagerly reading huge multipart payloads.
                if "multipart/form-data" not in content_type:
                    body = await request.body()
                    if len(body) > max_bytes:
                        request.state._captured_body = body[:max_bytes]
                        request.state._captured_body_truncated = True
                    else:
                        request.state._captured_body = body
                        request.state._captured_body_truncated = False

                    async def receive():
                        return {"type": "http.request", "body": body, "more_body": False}

                    # Starlette internal hook to allow downstream body reads.
                    request._receive = receive  # type: ignore[attr-defined]
                else:
                    request.state._captured_body = b"<multipart omitted>"
                    request.state._captured_body_truncated = False
            except Exception:
                # Never break requests due to logging capture.
                request.state._captured_body = b"<capture failed>"
                request.state._captured_body_truncated = False

            return await call_next(request)

        # Centralized 403 handler: log full (redacted) request + the validation
        # condition that failed, then return FastAPI's default response.
        @self.fastapi_app.exception_handler(HTTPException)
        async def _http_exception_handler(request: Request, exc: HTTPException):
            if exc.status_code == 403:
                await log_forbidden_request(request, exc)
            return await http_exception_handler(request, exc)

        # Add routers
        if self.task_service:
            task_router = create_task_router(
                self.task_service,
                allowed_users=live_allowed_users(self.config.secrets_path),
            )
            self.fastapi_app.include_router(task_router)
            logger.info("Task router registered")

        if self.webhook_service:
            webhook_router = create_webhook_router(
                self.webhook_service,
                allowed_users=live_allowed_users(self.config.secrets_path),
            )
            self.fastapi_app.include_router(webhook_router)
            logger.info("Webhook router registered")

        # Health check endpoint
        @self.fastapi_app.get("/health")
        async def health_check():
            """Health check endpoint."""
            stall_seconds = int(getattr(self.config, "health_stall_seconds", 180) or 180)
            snap = health_snapshot(stall_seconds=stall_seconds)
            if snap.status != "healthy" and bool(
                getattr(self.config, "health_fail_on_stall", True)
            ):
                raise HTTPException(status_code=503, detail=snap.to_dict(mode="json"))
            return snap.to_dict(mode="json")

        return self.fastapi_app

    async def start_background_services(self) -> None:
        """Start background services (message processor, telegram bot, cron scheduler).

        Starts the message processor, Telegram bot, and cron scheduler
        as background tasks.

        Requirements:
            - 6.1: Start cron scheduler on application startup
        """
        logger.info("Starting background services...")

        # Start stall watchdog early so a wedged tool can trigger a restart.
        # This is optional and should generally be enabled only when a process
        # supervisor (ECS/K8s/systemd) will restart on exit.
        try:
            start_stall_watchdog(
                stall_seconds=int(getattr(self.config, "health_stall_seconds", 180) or 180),
                enabled=bool(getattr(self.config, "self_restart_on_stall", False)),
            )
        except Exception:
            # Never break startup due to watchdog configuration.
            pass

        # Start Telegram bot FIRST (before message processor)
        # The typing callback requires the bot to be initialized
        if self.telegram_bot:
            await self.telegram_bot.start()
            logger.info("Telegram bot started")

        # Start message processor SECOND (after bot is ready)
        if self.message_processor:
            task = self.message_processor.start_background()
            self._background_tasks.append(task)
            logger.info("Message processor started")

        # Start cron scheduler
        if self.cron_scheduler:
            await self.cron_scheduler.start()
            logger.info("Cron scheduler started")

        # Start system scheduler (file cleanup)
        if self.system_scheduler:
            await self.system_scheduler.start()
            logger.info("System scheduler started")

    async def shutdown(self) -> None:
        """Gracefully shutdown all application components.

        Stops background services, closes database connections,
        and cleans up resources.

        Requirements:
            - System reliability: Graceful shutdown handling
        """
        logger.info("Initiating graceful shutdown...")

        # Signal shutdown
        self._shutdown_event.set()

        # Stop cron scheduler
        if self.cron_scheduler:
            await self.cron_scheduler.stop()
            logger.info("Cron scheduler stopped")

        # Stop system scheduler
        if self.system_scheduler:
            await self.system_scheduler.stop()
            logger.info("System scheduler stopped")

        # Stop message processor
        if self.message_processor:
            await self.message_processor.stop()
            logger.info("Message processor stopped")

        # Stop Telegram bot
        if self.telegram_bot:
            await self.telegram_bot.stop()
            logger.info("Telegram bot stopped")

        # Cancel background tasks
        for task in self._background_tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        logger.info("Background tasks cancelled")

        # Close database
        if self.database:
            await self.database.close()
            logger.info("Database connection closed")

        logger.info("Graceful shutdown complete")

    def setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown.

        Registers handlers for SIGINT and SIGTERM to trigger
        graceful shutdown.

        Requirements:
            - System reliability: Graceful shutdown handling
        """
        loop = asyncio.get_event_loop()

        def signal_handler(sig: signal.Signals) -> None:
            logger.info("Received signal %s, initiating shutdown...", sig.name)
            asyncio.create_task(self.shutdown())

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))

        logger.info("Signal handlers registered")


# Global application instance
_app: Application | None = None


def get_application() -> Application:
    """Get the global application instance.

    Returns:
        Application instance.

    Raises:
        RuntimeError: If application not initialized.
    """
    if _app is None:
        raise RuntimeError("Application not initialized")
    return _app


async def create_app(config: AgentConfig | None = None) -> Application:
    """Create and setup the application.

    Factory function for creating the application with all
    components initialized.

    Args:
        config: Optional configuration. If not provided, loads from
                config.json with environment variable overrides.

    Returns:
        Initialized Application instance.
    """
    global _app

    if config is None:
        config = AgentConfig.from_json_file()

    _app = Application(config)
    await _app.setup()
    _app.create_fastapi_app()

    return _app


def get_fastapi_app() -> FastAPI:
    """Get the FastAPI application instance.

    For use with ASGI servers like uvicorn.

    Returns:
        FastAPI application.

    Raises:
        RuntimeError: If application not initialized.
    """
    app = get_application()
    if app.fastapi_app is None:
        raise RuntimeError("FastAPI app not created")
    return app.fastapi_app


async def main(reload: bool = False) -> None:
    """Main entry point for running the application.

    Initializes all components and runs the application until
    shutdown is requested.

    Args:
        reload: Enable hot reload during development.
    """
    import uvicorn

    logger.info("Starting Mordecai...")

    try:
        # Load configuration
        config = AgentConfig.from_json_file()
        logger.info("Configuration loaded")

        # Create and setup application
        app = await create_app(config)

        # Start background services
        await app.start_background_services()

        logger.info(
            "Application running. API available at http://%s:%d",
            config.api_host,
            config.api_port,
        )

        # Create uvicorn config and server
        uvicorn_config = uvicorn.Config(
            app.fastapi_app,
            host=config.api_host,
            port=config.api_port,
            log_level="info",
            reload=reload,
        )

        # Ensure Uvicorn logging is configured, then suppress noisy healthcheck access logs.
        uvicorn_config.load()
        install_uvicorn_access_log_filters()

        server = uvicorn.Server(uvicorn_config)

        # Install signal handlers for graceful shutdown
        server.install_signal_handlers = lambda: None  # We handle signals

        # Run uvicorn server (this blocks until shutdown)
        await server.serve()

    except Exception as e:
        logger.exception("Application error: %s", e)
        raise
    finally:
        if _app:
            await _app.shutdown()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run Mordecai application")
    parser.add_argument("--reload", action="store_true", help="Enable hot reload")
    args = parser.parse_args()

    asyncio.run(main(reload=args.reload))
