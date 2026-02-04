"""SQS message consumption and routing.

This module handles consuming messages from per-user SQS queues
and routing them to the agent for processing.

Requirements:
- 12.3: Consume messages from each user's SQS_Queue
- 12.4: Route consumed messages to the Agent for processing
- 12.5: Retry failed messages according to SQS retry policy
- 12.6: Process messages in order for each user
- 6.3: Process file messages through same queue
- 5.1: Detect when agent creates files in working folder
- 5.2: Send generated files back to user
"""

import asyncio
import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from app.models.agent import AttachmentInfo
from app.sqs.typing_indicator import (
    ProgressUpdateLoop,
    ProgressUpdateSender,
    TypingIndicatorLoop,
    TypingIndicatorSender,
)
from app.tools import send_file as send_file_module
from app.tools import send_progress as send_progress_module

try:
    from mypy_boto3_sqs import SQSClient  # type: ignore[reportMissingImports]
except Exception:  # pragma: no cover

    class SQSClient(Protocol):
        def receive_message(self, **kwargs: Any) -> Any: ...

        def delete_message(self, **kwargs: Any) -> Any: ...

        def change_message_visibility(self, **kwargs: Any) -> Any: ...


if TYPE_CHECKING:
    from app.config import AgentConfig
    from app.dao.user_dao import UserDAO
    from app.services.agent_service import AgentService
    from app.services.file_service import FileService
    from app.sqs.queue_manager import SQSQueueManager

logger = logging.getLogger(__name__)


@dataclass
class QueueMessage:
    """Parsed message from SQS queue."""

    user_id: str
    message: str
    chat_id: int
    timestamp: datetime
    receipt_handle: str
    message_id: str
    attachments: list[AttachmentInfo] | None = None
    onboarding: dict[str, str | None] | None = None


def _parse_attachments(raw: Any) -> list[AttachmentInfo] | None:
    """Parse attachment payloads from SQS into typed models.

    The Telegram enqueue path serializes attachments into a list of dicts.
    Downstream agent code expects `AttachmentInfo` objects.
    """

    if not raw:
        return None

    if not isinstance(raw, list):
        logger.warning("Unexpected attachments payload type: %s", type(raw))
        return None

    parsed: list[AttachmentInfo] = []
    for item in raw:
        if isinstance(item, AttachmentInfo):
            parsed.append(item)
            continue
        if isinstance(item, dict):
            parsed.append(AttachmentInfo.model_validate(item))
            continue
        # Best-effort: ignore invalid items rather than crashing the worker.
        logger.warning("Ignoring invalid attachment item type: %s", type(item))

    return parsed or None


def _is_bedrock_tool_transcript_validation_error(exc: Exception) -> bool:
    """Heuristic for Bedrock ConverseStream tool transcript validation failures.

    These failures are deterministic for the offending transcript and are not
    helped by SQS retries. If we keep retrying them, we effectively poison the
    per-user queue.
    """

    msg = str(exc) or ""
    lowered = msg.lower()
    if "toolresult" in lowered and "tooluse" in lowered and "exceeds" in lowered:
        return True
    if "conversestream" in lowered and "validationexception" in lowered:
        return True
    return False


class MessageProcessor:
    """Processes messages from per-user SQS queues.

    Consumes messages from all user queues and routes them to the
    agent service for processing. Messages are processed in order
    for each user (FIFO within a queue).

    Uses a heartbeat mechanism to extend visibility timeout during
    long-running processing, preventing message redelivery.

    Requirements:
        - 12.3: Consume messages from each user's SQS_Queue
        - 12.4: Route consumed messages to the Agent for processing
        - 12.5: Retry failed messages according to SQS retry policy
        - 12.6: Process messages in order for each user
        - 6.3: Process file messages through same queue
        - 5.1: Detect when agent creates files in working folder
        - 5.2: Send generated files back to user
    """

    # Visibility timeout settings
    VISIBILITY_TIMEOUT = 900  # 15 minutes initial timeout
    HEARTBEAT_INTERVAL = 60  # Extend every 60 seconds
    HEARTBEAT_EXTENSION = 120  # Extend by 2 minutes each time

    def __init__(
        self,
        sqs_client: "SQSClient",
        queue_manager: "SQSQueueManager",
        agent_service: "AgentService",
        config: "AgentConfig | None" = None,
        user_dao: "UserDAO | None" = None,
        response_callback: Callable[[int, str], Any] | None = None,
        file_send_callback: (Callable[[int, str | Path, str | None], Any] | None) = None,
        progress_callback: (Callable[[int, str], Any] | None) = None,
        typing_action_callback: (Callable[[int, str], Any] | None) = None,
        file_service: "FileService | None" = None,
        polling_interval: float = 1.0,
        max_workers: int = 10,
        max_prefetch_per_queue: int = 2,
        max_inflight_total: int = 50,
    ) -> None:
        """Initialize the message processor.

        Args:
            sqs_client: Boto3 SQS client.
            queue_manager: Queue manager for getting queue URLs.
            agent_service: Agent service for processing messages.
            config: Agent config for parallel processing settings.
            user_dao: User DAO for ensuring users exist in database.
            response_callback: Optional callback for sending responses
                (e.g., to Telegram). Signature: (chat_id, response) -> Any
            file_send_callback: Optional callback for sending files
                (e.g., to Telegram).
                Signature: (chat_id, file_path, caption) -> Any
            progress_callback: Optional callback for sending progress updates
                (e.g., to Telegram). Signature: (chat_id, message) -> Any
            typing_action_callback: Optional callback for sending chat actions
                (e.g., typing indicator). Signature: (chat_id, action) -> Any
            file_service: Optional file service for working folder access.
            polling_interval: Seconds between polling cycles (default: 1.0).
            max_workers: Max concurrent workers for processing (default: 10).
        """
        self.sqs_client = sqs_client
        self.queue_manager = queue_manager
        self.agent_service = agent_service
        self.config = config
        self.user_dao = user_dao
        self.response_callback = response_callback
        self.file_send_callback = file_send_callback
        self.progress_callback = progress_callback
        self.typing_action_callback = typing_action_callback
        self.file_service = file_service
        self.polling_interval = polling_interval
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.running = False
        self._processing_task: asyncio.Task | None = None
        self._heartbeat_tasks: dict[str, asyncio.Task] = {}
        # Per-user semaphores for parallel message processing (replaces _queue_locks)
        self._user_semaphores: dict[str, asyncio.Semaphore] = {}
        self._semaphore_lock = asyncio.Lock()
        self._queue_prefetch_semaphores: dict[str, asyncio.Semaphore] = {}
        self._inflight_total_semaphore = asyncio.Semaphore(max_inflight_total)
        self._max_prefetch_per_queue = max_prefetch_per_queue
        self._message_tasks: set[asyncio.Task] = set()

        # Get max concurrent tasks per user from config (default to 5 for backward compatibility)
        self._max_concurrent_per_user = config.max_concurrent_tasks_per_user if config else 5

        # A short, user-friendly ack for when messages arrive while a previous
        # message from the same queue is still being processed.
        self._busy_ack_text = (
            "I’m still working on your previous request. "
            "I queued this one and will reply as soon as I’m done."
        )

        # Diagnostics: log background polling mode once per queue.
        self._logged_background_queues: set[str] = set()

    async def start(self) -> None:
        """Start processing messages from all user queues.

        Continuously polls all registered user queues and processes
        messages. Messages are processed in order for each user.

        Requirements:
            - 12.3: Consume messages from each user's SQS_Queue
            - 12.6: Process messages in order for each user
        """
        logger.info("Starting message processor")
        logger.info(
            "Message processor mode: background_polling=true max_prefetch_per_queue=%s max_inflight_total=%s",
            self._max_prefetch_per_queue,
            getattr(self._inflight_total_semaphore, "_value", "?"),
        )
        self.running = True

        while self.running:
            queue_urls = self.queue_manager.get_all_queue_urls()

            if queue_urls:
                # Process all queues concurrently
                tasks = [self._process_queue(url, background=True) for url in queue_urls]
                await asyncio.gather(*tasks, return_exceptions=True)

            await asyncio.sleep(self.polling_interval)

        logger.info("Message processor stopped")

    async def stop(self) -> None:
        """Stop the message processor gracefully."""
        logger.info("Stopping message processor")
        self.running = False

        if self._processing_task:
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass

        # Cancel any in-flight message tasks.
        tasks = list(self._message_tasks)
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Cancel any orphaned heartbeat tasks (normally cleaned up by message tasks).
        for heartbeat in list(self._heartbeat_tasks.values()):
            heartbeat.cancel()
        if self._heartbeat_tasks:
            await asyncio.gather(*self._heartbeat_tasks.values(), return_exceptions=True)
        self._heartbeat_tasks.clear()

    async def _get_user_semaphore(self, user_id: str) -> asyncio.Semaphore:
        """Get or create a semaphore for the given user.

        The semaphore limits the number of concurrent messages processed
        for each user, enabling parallel processing while preventing
        resource exhaustion.
        """
        async with self._semaphore_lock:
            if user_id not in self._user_semaphores:
                self._user_semaphores[user_id] = asyncio.Semaphore(self._max_concurrent_per_user)
            return self._user_semaphores[user_id]

    def _get_queue_prefetch_semaphore(self, queue_url: str) -> asyncio.Semaphore:
        sem = self._queue_prefetch_semaphores.get(queue_url)
        if sem is None:
            sem = asyncio.Semaphore(self._max_prefetch_per_queue)
            self._queue_prefetch_semaphores[queue_url] = sem
        return sem

    async def _process_queue(self, queue_url: str, *, background: bool = False) -> None:
        """Process messages from a single queue.

        Receives up to 1 message at a time to maintain order.
        Uses long polling (5 seconds) for efficiency.

        Args:
            queue_url: URL of the SQS queue to process.

        Requirements:
            - 12.3: Consume messages from each user's SQS_Queue
            - 12.6: Process messages in order for each user
        """
        if not background:
            # Legacy / test-friendly behavior: receive and fully process a single message.
            try:
                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(
                    self.executor,
                    lambda: self.sqs_client.receive_message(
                        QueueUrl=queue_url,
                        MaxNumberOfMessages=1,
                        WaitTimeSeconds=5,
                        AttributeNames=["All"],
                        MessageAttributeNames=["All"],
                    ),
                )

                messages = response.get("Messages", [])
                for message in messages:
                    await self._handle_message_parallel(queue_url, message)

            except Exception as e:
                logger.error("Error processing queue %s: %s", queue_url, e)
            return

        # Background polling behavior (used by start()):
        # - Keeps polling responsive even when a long-running agent call is in progress.
        # - Uses per-user semaphores to allow parallel processing up to a configurable limit.
        # - Limits prefetch (number of reserved/invisible SQS messages) per queue.
        queue_prefetch_sem = self._get_queue_prefetch_semaphore(queue_url)

        if queue_url not in self._logged_background_queues:
            self._logged_background_queues.add(queue_url)
            logger.info(
                "Queue poller active (background=true) queue=%s max_prefetch_per_queue=%s max_concurrent_per_user=%s",
                queue_url,
                self._max_prefetch_per_queue,
                self._max_concurrent_per_user,
            )

        # Avoid reserving more messages than we can safely heartbeat.
        if queue_prefetch_sem.locked() or self._inflight_total_semaphore.locked():
            return

        acquired_total = False
        acquired_queue = False
        try:
            await self._inflight_total_semaphore.acquire()
            acquired_total = True
            await queue_prefetch_sem.acquire()
            acquired_queue = True

            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                self.executor,
                lambda: self.sqs_client.receive_message(
                    QueueUrl=queue_url,
                    MaxNumberOfMessages=1,
                    WaitTimeSeconds=5,
                    AttributeNames=["All"],
                    MessageAttributeNames=["All"],
                ),
            )
            messages = response.get("Messages", [])
            if not messages:
                return

            # We reserved capacity for exactly one message above.
            message = messages[0]

            async def runner() -> None:
                try:
                    await self._handle_message_parallel(queue_url, message)
                finally:
                    # Release prefetch capacity after the message is fully processed.
                    queue_prefetch_sem.release()
                    self._inflight_total_semaphore.release()

            task = asyncio.create_task(runner())
            self._message_tasks.add(task)

            def _cleanup(t: asyncio.Task) -> None:
                self._message_tasks.discard(t)

            task.add_done_callback(_cleanup)

            # Important: do NOT await the task here. This is what keeps polling responsive.
            acquired_total = False
            acquired_queue = False

        except Exception as e:
            logger.error("Error processing queue %s: %s", queue_url, e)
        finally:
            # If we acquired capacity but didn't schedule a runner, release it.
            if acquired_queue:
                queue_prefetch_sem.release()
            if acquired_total:
                self._inflight_total_semaphore.release()

    async def _maybe_send_busy_ack(
        self, body: dict[str, Any], *, user_semaphore: asyncio.Semaphore
    ) -> None:
        """Send a busy acknowledgment if the user's semaphore is at capacity.

        This informs the user that their message has been queued because
        they have reached their concurrent task limit.
        """
        response_cb = self.response_callback
        if not response_cb:
            return

        # Check if semaphore is at capacity (all slots taken)
        if user_semaphore._value > 0:
            # There's still capacity, no need for busy ack
            return

        raw_chat_id = body.get("chat_id")
        if raw_chat_id is None:
            return

        try:
            chat_id = int(raw_chat_id)
        except Exception:
            return

        try:
            res = response_cb(chat_id, self._busy_ack_text)
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            logger.exception("Failed sending busy ack")

    async def _handle_message_parallel(
        self,
        queue_url: str,
        message: dict,
    ) -> str | None:
        """Handle a message with heartbeat + per-user concurrency limit.

        Uses a per-user semaphore to allow parallel processing up to a
        configurable limit. Heartbeat starts immediately upon receipt.

        This replaces the old _handle_message_ordered which used a lock
        for strict sequential processing.
        """
        message_id = message.get("MessageId", "unknown")
        receipt_handle = message["ReceiptHandle"]

        # Parse body early to get user_id for semaphore and for potential "queued" ack.
        body: dict[str, Any] | None = None
        user_id: str | None = None
        chat_id: int | None = None
        try:
            body = json.loads(message.get("Body", "{}"))
            user_id = body.get("user_id") if body else None
            chat_id = body.get("chat_id") if body else None
        except Exception:
            body = None

        # If we couldn't parse user_id, we can't use the semaphore - process anyway
        # but skip the busy ack and semaphore acquisition.
        if not user_id:
            logger.warning("No user_id in message %s, processing without semaphore", message_id)

        heartbeat_task = asyncio.create_task(
            self._start_heartbeat(queue_url, receipt_handle, message_id)
        )
        self._heartbeat_tasks[message_id] = heartbeat_task

        # Typing indicator loop (will be started if typing_action_callback is available)
        typing_loop: TypingIndicatorLoop | None = None

        # Progress update loop (will be started if progress_callback is available)
        progress_loop: ProgressUpdateLoop | None = None

        # Start typing indicator IMMEDIATELY (before semaphore acquisition)
        # This ensures the user sees visual feedback as soon as SQS starts processing
        if self.typing_action_callback and chat_id:
            typing_cbk = self.typing_action_callback

            async def typing_action_cb(c_id: int, action: str) -> None:
                result = typing_cbk(c_id, action)
                if asyncio.iscoroutine(result):
                    await result

            sender = TypingIndicatorSender(typing_action_cb)
            typing_loop = TypingIndicatorLoop(sender, chat_id)
            await typing_loop.start()

        try:
            # Get the user's semaphore and potentially send busy ack if at capacity
            if user_id and body:
                user_semaphore = await self._get_user_semaphore(user_id)
                await self._maybe_send_busy_ack(body, user_semaphore=user_semaphore)

            # Acquire the semaphore to limit concurrent processing per user
            if user_id:
                user_semaphore = await self._get_user_semaphore(user_id)
                async with user_semaphore:
                    # Set up progress callback if available
                    if self.progress_callback and chat_id:
                        progress_cbk = self.progress_callback

                        async def progress_cb(message: str) -> bool:
                            result = progress_cbk(chat_id, message)
                            if asyncio.iscoroutine(result):
                                return await result
                            return bool(result)

                        send_progress_module.set_progress_callback(progress_cb)

                        # Send an immediate one-shot progress update so the user
                        # gets a fast acknowledgment even if the agent/tools take
                        # minutes (e.g., transcript fetching).
                        try:
                            initial = progress_cbk(chat_id, "Working on it…")
                            if asyncio.iscoroutine(initial):
                                await initial
                        except Exception:
                            logger.debug("Failed to send initial progress ack", exc_info=True)

                        # Start progress update loop AFTER callback is set
                        # Pass a lambda that captures the current context
                        progress_update_sender = ProgressUpdateSender(progress_cbk)
                        progress_loop = ProgressUpdateLoop(
                            progress_update_sender,
                            chat_id,
                            lambda: send_progress_module.get_pending_progress_messages(),
                        )
                        await progress_loop.start()

                    return await self._handle_message(queue_url, message, body=body)
            else:
                # No user_id, process directly (shouldn't happen in normal flow)
                return await self._handle_message(queue_url, message, body=body)
        finally:
            self._stop_heartbeat(message_id)
            # Stop typing indicator loop
            if typing_loop:
                await typing_loop.stop()
            # Stop progress update loop
            if progress_loop:
                await progress_loop.stop()
            # Clear per-task tool callbacks and pending file queue.
            send_file_module.clear_send_callbacks()
            send_progress_module.clear_progress_callback()

    async def _start_heartbeat(self, queue_url: str, receipt_handle: str, message_id: str) -> None:
        """Start a heartbeat task to extend visibility timeout.

        Periodically extends the message visibility timeout to prevent
        redelivery during long-running processing.

        Args:
            queue_url: URL of the queue.
            receipt_handle: Receipt handle of the message.
            message_id: Message ID for logging.
        """
        try:
            while True:
                await asyncio.sleep(self.HEARTBEAT_INTERVAL)
                logger.debug(
                    "Extending visibility timeout for message %s",
                    message_id,
                )
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    self.executor,
                    lambda: self.sqs_client.change_message_visibility(
                        QueueUrl=queue_url,
                        ReceiptHandle=receipt_handle,
                        VisibilityTimeout=self.HEARTBEAT_EXTENSION,
                    ),
                )
                logger.debug(
                    "Extended visibility timeout for message %s by %ds",
                    message_id,
                    self.HEARTBEAT_EXTENSION,
                )
        except asyncio.CancelledError:
            logger.debug("Heartbeat cancelled for message %s", message_id)
        except Exception as e:
            logger.warning(
                "Failed to extend visibility for message %s: %s",
                message_id,
                e,
            )

    def _stop_heartbeat(self, message_id: str) -> None:
        """Stop the heartbeat task for a message.

        Args:
            message_id: Message ID to stop heartbeat for.
        """
        task = self._heartbeat_tasks.pop(message_id, None)
        if task:
            task.cancel()

    async def _handle_message(
        self, queue_url: str, message: dict, *, body: dict[str, Any] | None = None
    ) -> str | None:
        """Route message to agent and handle response.

        Parses the message, sends it to the agent for processing,
        and deletes the message from the queue on success.
        Failed messages are left in the queue for retry per SQS policy.

        Starts a heartbeat task to extend visibility timeout during
        processing, preventing message redelivery for long-running tasks.

        When attachments are present, calls process_message_with_attachments().
        After processing, detects any new files in the user's working folder
        and sends them back to the user.

        Args:
            queue_url: URL of the source queue.
            message: Raw SQS message dict.

        Returns:
            Agent response text, or None if processing failed.

        Requirements:
            - 12.4: Route consumed messages to the Agent for processing
            - 12.5: Retry failed messages according to SQS retry policy
            - 6.3: Process file messages through same queue
            - 5.1: Detect when agent creates files in working folder
            - 5.2: Send generated files back to user
        """
        message_id = message.get("MessageId", "unknown")
        receipt_handle = message["ReceiptHandle"]

        try:
            # Parse message body
            if body is None:
                body = json.loads(message["Body"])

            # Type guard: at this point we require a dict.
            if body is None:
                return None

            # Parse attachments if present (Requirement 6.3)
            attachments = _parse_attachments(body.get("attachments"))

            # Parse onboarding context if present (first interaction)
            onboarding = body.get("onboarding")

            parsed = QueueMessage(
                user_id=body["user_id"],
                message=body["message"],
                chat_id=body["chat_id"],
                timestamp=datetime.fromisoformat(body["timestamp"]),
                receipt_handle=receipt_handle,
                message_id=message_id,
                attachments=attachments,
                onboarding=onboarding,
            )

            logger.info(
                "Processing message %s for user %s (attachments: %d)",
                message_id,
                parsed.user_id,
                len(attachments) if attachments else 0,
            )

            # Ensure user exists in database with their telegram_id (chat_id)
            if self.user_dao:
                await self.user_dao.get_or_create(
                    user_id=parsed.user_id,
                    telegram_id=str(parsed.chat_id),
                )

            # Get files in working folder before processing (for detection)
            files_before = self._get_working_folder_files(parsed.user_id)

            # Set up send_file tool callbacks before agent runs.
            # Bind to a local to keep type narrowing inside nested closures.
            file_send_cbk = self.file_send_callback
            if file_send_cbk is not None:

                async def send_file_cb(path: str, caption: str | None) -> bool:
                    result = file_send_cbk(parsed.chat_id, path, caption)
                    if asyncio.iscoroutine(result):
                        return await result
                    return bool(result)

                async def send_photo_cb(path: str, caption: str | None) -> bool:
                    # Use same callback - TelegramBot handles photo vs doc
                    result = file_send_cbk(parsed.chat_id, path, caption)
                    if asyncio.iscoroutine(result):
                        return await result
                    return bool(result)

                send_file_module.set_send_callbacks(send_file_cb, send_photo_cb)

            # Route to agent for processing
            if parsed.attachments:
                # Process with attachments (Requirement 6.3)
                response = await self.agent_service.process_message_with_attachments(
                    user_id=parsed.user_id,
                    message=parsed.message,
                    attachments=parsed.attachments,
                    onboarding_context=parsed.onboarding,
                )
            else:
                # Process regular message (with onboarding context if first interaction)
                response = await self.agent_service.process_message(
                    user_id=parsed.user_id,
                    message=parsed.message,
                    onboarding_context=parsed.onboarding,
                )

            # Send response via callback if provided
            if self.response_callback:
                try:
                    # Ensure we always have a response to send (even if empty/None)
                    # This prevents silent failures where the user gets no feedback
                    if not response:
                        response = (
                            "I processed your request but couldn't generate a response. "
                            "This might be due to a timeout, command failure, or an internal issue. "
                            "Please try again or rephrase your request."
                        )
                        logger.warning(
                            "Empty or None response for message %s; sending fallback message",
                            message_id,
                        )
                    # Allow out-of-order heavy jobs while still letting users identify
                    # which reply maps to which inbound message.
                    tagged = f"[job {message_id[:8]}] {response}"
                    callback_result = self.response_callback(parsed.chat_id, tagged)
                    # Handle async callbacks
                    if asyncio.iscoroutine(callback_result):
                        await callback_result
                except Exception as e:
                    logger.error(
                        "Response callback failed for message %s: %s",
                        message_id,
                        e,
                    )

            # Detect and send new files (Requirements 5.1, 5.2)
            await self._send_generated_files(
                parsed.user_id,
                parsed.chat_id,
                files_before,
            )

            # Send files queued by send_file tool
            await self._send_pending_files(parsed.chat_id)

            # Delete message on successful processing
            await self._delete_message(queue_url, receipt_handle)

            logger.info("Successfully processed message %s", message_id)

            return response

        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in message %s: %s", message_id, e)
            # Delete malformed messages (can't be retried)
            await self._delete_message(queue_url, receipt_handle)
            return None

        except KeyError as e:
            logger.error(
                "Missing required field in message %s: %s",
                message_id,
                e,
            )
            # Delete malformed messages (can't be retried)
            await self._delete_message(queue_url, receipt_handle)
            return None

        except Exception as e:
            if _is_bedrock_tool_transcript_validation_error(e):
                # Deterministic failure: retries will keep failing and poison the queue.
                logger.error(
                    "Bedrock tool transcript validation failed for message %s: %s. Deleting message to avoid poison retries.",
                    message_id,
                    e,
                )
                try:
                    if self.response_callback is not None:
                        chat_id: int | None = None
                        try:
                            raw_chat_id = body.get("chat_id") if body else None
                            if isinstance(raw_chat_id, int):
                                chat_id = raw_chat_id
                            elif isinstance(raw_chat_id, str):
                                chat_id = int(raw_chat_id)
                        except Exception:
                            chat_id = None
                        if chat_id is not None:
                            await self.response_callback(
                                chat_id,
                                "I hit an internal tool-streaming error while processing that request. Please resend your last message.",
                            )
                except Exception as callback_err:
                    logger.error(
                        "Response callback failed for message %s after Bedrock validation error: %s",
                        message_id,
                        callback_err,
                    )
                await self._delete_message(queue_url, receipt_handle)
                return None

            # Leave message in queue for retry (SQS retry policy)
            logger.error(
                "Failed to process message %s: %s. Message will be retried per SQS policy.",
                message_id,
                e,
            )
            return None

        finally:
            # Heartbeat and per-task tool state are cleared by _handle_message_ordered().
            pass

    def _get_working_folder_files(self, user_id: str) -> set[Path]:
        """Get current files in user's working folder.

        Args:
            user_id: User's telegram ID.

        Returns:
            Set of file paths in the working folder.

        Requirements:
            - 5.1: Detect when agent creates files in working folder
        """
        if self.file_service is None:
            return set()

        try:
            working_dir = self.file_service.get_user_working_dir(user_id)
            if working_dir.exists():
                return {f for f in working_dir.iterdir() if f.is_file()}
        except Exception as e:
            logger.warning(
                "Failed to list working folder for user %s: %s",
                user_id,
                e,
            )

        return set()

    async def _send_generated_files(
        self,
        user_id: str,
        chat_id: int,
        files_before: set[Path],
    ) -> None:
        """Detect and send files created by the agent.

        Compares files in working folder before and after processing
        to detect newly created files, then sends them to the user.

        Args:
            user_id: User's telegram ID.
            chat_id: Telegram chat ID for sending files.
            files_before: Set of files that existed before processing.

        Requirements:
            - 5.1: Detect when agent creates files in working folder
            - 5.2: Send generated files back to user
        """
        if self.file_service is None or self.file_send_callback is None:
            return

        try:
            # Get current files
            files_after = self._get_working_folder_files(user_id)

            # Find new files
            new_files = files_after - files_before

            if not new_files:
                return

            logger.info(
                "Detected %d new files for user %s: %s",
                len(new_files),
                user_id,
                [f.name for f in new_files],
            )

            # Send each new file
            for file_path in sorted(new_files):
                try:
                    caption = f"📎 Generated file: {file_path.name}"
                    callback_result = self.file_send_callback(
                        chat_id,
                        file_path,
                        caption,
                    )
                    # Handle async callbacks
                    if asyncio.iscoroutine(callback_result):
                        callback_result = await callback_result

                    if callback_result:
                        logger.info(
                            "Sent generated file to user %s: %s",
                            user_id,
                            file_path.name,
                        )
                    else:
                        logger.warning(
                            "Failed to send generated file to user %s: %s",
                            user_id,
                            file_path.name,
                        )
                except Exception as e:
                    logger.error(
                        "Failed to send file %s to user %s: %s",
                        file_path.name,
                        user_id,
                        e,
                    )

        except Exception as e:
            logger.error(
                "Error detecting generated files for user %s: %s",
                user_id,
                e,
            )

    async def _send_pending_files(self, chat_id: int) -> None:
        """Send files queued by the send_file tool.

        The send_file tool queues files during agent execution.
        This method sends them after the agent response.

        Args:
            chat_id: Telegram chat ID for sending files.
        """
        if self.file_send_callback is None:
            return

        pending = send_file_module.get_pending_files()
        if not pending:
            return

        logger.info("Sending %d pending files", len(pending))

        for file_info in pending:
            try:
                file_path = Path(file_info["path"])
                caption = file_info.get("caption")

                if not file_path.exists():
                    logger.warning("Pending file not found: %s", file_path)
                    continue

                callback_result = self.file_send_callback(
                    chat_id,
                    file_path,
                    caption,
                )
                if asyncio.iscoroutine(callback_result):
                    callback_result = await callback_result

                if callback_result:
                    logger.info("Sent pending file: %s", file_path.name)
                else:
                    logger.warning("Failed to send pending file: %s", file_path.name)

            except Exception as e:
                logger.error(
                    "Failed to send pending file %s: %s",
                    file_info.get("path"),
                    e,
                )

    async def _delete_message(self, queue_url: str, receipt_handle: str) -> None:
        """Delete a message from the queue.

        Args:
            queue_url: URL of the queue.
            receipt_handle: Receipt handle of the message to delete.
        """
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self.executor,
                lambda: self.sqs_client.delete_message(
                    QueueUrl=queue_url,
                    ReceiptHandle=receipt_handle,
                ),
            )
        except Exception as e:
            logger.error("Failed to delete message from %s: %s", queue_url, e)

    def start_background(self) -> asyncio.Task:
        """Start the processor as a background task.

        Returns:
            The asyncio Task running the processor.
        """
        self._processing_task = asyncio.create_task(self.start())
        return self._processing_task
