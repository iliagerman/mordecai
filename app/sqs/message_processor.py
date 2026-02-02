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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from app.tools import send_file as send_file_module

if TYPE_CHECKING:
    from mypy_boto3_sqs import SQSClient

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
    attachments: list[dict] | None = None
    onboarding: dict[str, str | None] | None = None


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
        user_dao: "UserDAO | None" = None,
        response_callback: Callable[[int, str], Any] | None = None,
        file_send_callback: (Callable[[int, str | Path, str | None], Any] | None) = None,
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
            user_dao: User DAO for ensuring users exist in database.
            response_callback: Optional callback for sending responses
                (e.g., to Telegram). Signature: (chat_id, response) -> Any
            file_send_callback: Optional callback for sending files
                (e.g., to Telegram).
                Signature: (chat_id, file_path, caption) -> Any
            file_service: Optional file service for working folder access.
            polling_interval: Seconds between polling cycles (default: 1.0).
            max_workers: Max concurrent workers for processing (default: 10).
        """
        self.sqs_client = sqs_client
        self.queue_manager = queue_manager
        self.agent_service = agent_service
        self.user_dao = user_dao
        self.response_callback = response_callback
        self.file_send_callback = file_send_callback
        self.file_service = file_service
        self.polling_interval = polling_interval
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.running = False
        self._processing_task: asyncio.Task | None = None
        self._heartbeat_tasks: dict[str, asyncio.Task] = {}
        self._queue_locks: dict[str, asyncio.Lock] = {}
        self._queue_prefetch_semaphores: dict[str, asyncio.Semaphore] = {}
        self._inflight_total_semaphore = asyncio.Semaphore(max_inflight_total)
        self._max_prefetch_per_queue = max_prefetch_per_queue
        self._message_tasks: set[asyncio.Task] = set()

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

    def _get_queue_lock(self, queue_url: str) -> asyncio.Lock:
        lock = self._queue_locks.get(queue_url)
        if lock is None:
            lock = asyncio.Lock()
            self._queue_locks[queue_url] = lock
        return lock

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
                    await self._handle_message_ordered(queue_url, message)

            except Exception as e:
                logger.error("Error processing queue %s: %s", queue_url, e)
            return

        # Background polling behavior (used by start()):
        # - Keeps polling responsive even when a long-running agent call is in progress.
        # - Preserves in-queue ordering by serializing per-queue handling with a lock.
        # - Limits prefetch (number of reserved/invisible SQS messages) per queue.
        queue_lock = self._get_queue_lock(queue_url)
        queue_prefetch_sem = self._get_queue_prefetch_semaphore(queue_url)

        if queue_url not in self._logged_background_queues:
            self._logged_background_queues.add(queue_url)
            logger.info(
                "Queue poller active (background=true) queue=%s max_prefetch_per_queue=%s",
                queue_url,
                self._max_prefetch_per_queue,
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
                    await self._handle_message_ordered(queue_url, message, queue_lock=queue_lock)
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

    async def _maybe_send_busy_ack(self, body: dict[str, Any], *, queue_lock: asyncio.Lock) -> None:
        if not self.response_callback:
            return

        if not queue_lock.locked():
            return

        try:
            chat_id = int(body.get("chat_id"))
        except Exception:
            return

        try:
            res = self.response_callback(chat_id, self._busy_ack_text)
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            logger.exception("Failed sending busy ack")

    async def _handle_message_ordered(
        self,
        queue_url: str,
        message: dict,
        *,
        queue_lock: asyncio.Lock | None = None,
    ) -> str | None:
        """Handle a message with heartbeat + per-queue ordering.

        Heartbeat starts immediately upon receipt (before waiting on the per-queue lock),
        so prefetched messages won't reappear while waiting behind a long-running task.
        """
        if queue_lock is None:
            queue_lock = self._get_queue_lock(queue_url)

        message_id = message.get("MessageId", "unknown")
        receipt_handle = message["ReceiptHandle"]

        # Parse body early so we can potentially send a "queued" ack while busy.
        body: dict[str, Any] | None = None
        try:
            body = json.loads(message.get("Body", "{}"))
        except Exception:
            body = None

        heartbeat_task = asyncio.create_task(
            self._start_heartbeat(queue_url, receipt_handle, message_id)
        )
        self._heartbeat_tasks[message_id] = heartbeat_task

        try:
            if body is not None:
                await self._maybe_send_busy_ack(body, queue_lock=queue_lock)

            async with queue_lock:
                return await self._handle_message(queue_url, message, body=body)
        finally:
            self._stop_heartbeat(message_id)
            # Clear per-task tool callbacks and pending file queue.
            send_file_module.clear_send_callbacks()

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

            # Parse attachments if present (Requirement 6.3)
            attachments = body.get("attachments")

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

            # Set up send_file tool callbacks before agent runs
            if self.file_send_callback is not None:

                async def send_file_cb(path: str, caption: str | None) -> bool:
                    result = self.file_send_callback(parsed.chat_id, path, caption)
                    if asyncio.iscoroutine(result):
                        return await result
                    return bool(result)

                async def send_photo_cb(path: str, caption: str | None) -> bool:
                    # Use same callback - TelegramBot handles photo vs doc
                    result = self.file_send_callback(parsed.chat_id, path, caption)
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
            if self.response_callback and response:
                try:
                    callback_result = self.response_callback(parsed.chat_id, response)
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
                        await callback_result

                    logger.info(
                        "Sent generated file to user %s: %s",
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
                    await callback_result

                logger.info("Sent pending file: %s", file_path.name)

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
