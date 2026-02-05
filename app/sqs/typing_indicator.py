"""Typing indicator for Telegram bot.

This module provides a background task that sends typing actions periodically
to show the user that the bot is working on their request.
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class TypingIndicatorSender:
    """Simple callback-based sender for typing actions.

    Wraps an async callback that accepts (chat_id, action) parameters.
    """

    def __init__(
        self,
        callback: Callable[[int, str], Awaitable[Any | None]],
    ) -> None:
        """Initialize the sender with a callback.

        Args:
            callback: Async function that takes (chat_id, action) and sends
                the chat action to Telegram.
        """
        self.callback = callback

    async def send_chat_action(
        self, chat_id: int | str, action: str
    ) -> Any | None:
        """Send a chat action via the callback."""
        return await self.callback(int(chat_id), action)


class TypingIndicatorLoop:
    """Background task that sends typing actions periodically.

    Telegram chat actions expire after about 5 seconds, so we need to
    resend them periodically to keep the indicator visible.

    The loop runs until stopped, sending the specified action at regular
    intervals.
    """

    # Default interval: 4 seconds (action expires in ~5 seconds)
    DEFAULT_INTERVAL_SECONDS = 4.0

    # Supported action types (must map to valid ChatAction values)
    ACTION_TYPING = "typing"
    ACTION_UPLOAD_DOCUMENT = "upload_document"
    ACTION_UPLOAD_PHOTO = "upload_photo"
    ACTION_RECORD_VIDEO = "record_video"
    ACTION_FIND_LOCATION = "find_location"

    def __init__(
        self,
        sender: TypingIndicatorSender,
        chat_id: int,
        action: str = ACTION_TYPING,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        """Initialize the typing indicator loop.

        Args:
            sender: Object that can send chat actions via send_chat_action method.
            chat_id: Telegram chat ID to send actions to.
            action: Chat action type (typing, upload_document, etc.).
            interval_seconds: How often to resend the action.
        """
        self.sender = sender
        self.chat_id = chat_id
        self.action = action
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        """Start the typing indicator loop."""
        self._stop_event.clear()

        async def _loop() -> None:
            try:
                # Send immediately on start
                await self._send_action()

                # Then send periodically until stopped
                while not self._stop_event.is_set():
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=self.interval_seconds,
                        )
                        break  # Stop event was set
                    except asyncio.TimeoutError:
                        # Timeout elapsed, send another action
                        await self._send_action()
            except asyncio.CancelledError:
                logger.debug("Typing indicator loop cancelled")
            except Exception as e:
                logger.warning("Typing indicator loop error: %s", e)

        self._task = asyncio.create_task(_loop())

    async def _send_action(self) -> None:
        """Send the chat action."""
        try:
            await self.sender.send_chat_action(self.chat_id, self.action)
            logger.debug(
                "Sent %s action to chat %s",
                self.action,
                self.chat_id,
            )
        except Exception as e:
            logger.warning(
                "Failed to send %s action to chat %s: %s",
                self.action,
                self.chat_id,
                e,
            )

    async def stop(self) -> None:
        """Stop the typing indicator loop."""
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.debug("Typing indicator stopped for chat %s", self.chat_id)


class ProgressUpdateSender:
    """Sender for progress updates during agent execution.

    Wraps an async callback that accepts (chat_id, message) parameters.
    """

    def __init__(
        self,
        callback: Callable[[int, str], Awaitable[Any | None]],
    ) -> None:
        """Initialize the sender with a callback.

        Args:
            callback: Async function that takes (chat_id, message) and sends
                the progress update to Telegram.
        """
        self.callback = callback

    async def send_progress(
        self, chat_id: int, message: str
    ) -> Any | None:
        """Send a progress update via the callback."""
        return await self.callback(int(chat_id), message)


class ProgressUpdateLoop:
    """Background task that sends pending progress updates periodically.

    The agent queues progress updates during execution in a background thread.
    This loop periodically checks for and sends any queued updates to Telegram,
    providing the user with real-time feedback during long-running operations.
    """

    # Default interval: 2 seconds (check frequently for updates)
    DEFAULT_INTERVAL_SECONDS = 2.0

    def __init__(
        self,
        sender: ProgressUpdateSender,
        chat_id: int,
        get_pending_messages: Callable[[], list[str]],
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        """Initialize the progress update loop.

        Args:
            sender: Object that can send progress updates via send_progress method.
            chat_id: Telegram chat ID to send updates to.
            get_pending_messages: Callable that returns pending progress messages.
            interval_seconds: How often to check for and send pending progress updates.
        """
        self.sender = sender
        self.chat_id = chat_id
        self.get_pending_messages = get_pending_messages
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._sent_messages: set[str] = set()  # Track sent messages to avoid duplicates

    async def start(self) -> None:
        """Start the progress update loop."""
        self._stop_event.clear()
        self._sent_messages.clear()

        async def _loop() -> None:
            try:
                while not self._stop_event.is_set():
                    # Get pending messages (this also clears the queue)
                    pending = self.get_pending_messages()

                    # Send any new messages
                    for message in pending:
                        if message not in self._sent_messages:
                            await self._send_progress(message)
                            self._sent_messages.add(message)

                    # Wait for next check or stop event
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=self.interval_seconds,
                        )
                        break  # Stop event was set
                    except asyncio.TimeoutError:
                        # Timeout elapsed, check for more updates
                        continue
            except asyncio.CancelledError:
                logger.debug("Progress update loop cancelled")
            except Exception as e:
                logger.warning("Progress update loop error: %s", e)

        self._task = asyncio.create_task(_loop())

    async def _send_progress(self, message: str) -> None:
        """Send a progress update."""
        try:
            await self.sender.send_progress(self.chat_id, message)
            logger.debug(
                "Sent progress update to chat %s: %s",
                self.chat_id,
                message[:50],  # Truncate for log
            )
        except Exception as e:
            logger.warning(
                "Failed to send progress update to chat %s: %s",
                self.chat_id,
                e,
            )

    async def stop(self) -> None:
        """Stop the progress update loop."""
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.debug("Progress update loop stopped for chat %s", self.chat_id)
