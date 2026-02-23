"""Credential approval manager for interactive user consent.

When the agent needs to access a credential (e.g., 1Password), it requests
approval from the user via Telegram inline buttons. The approval flow is:

1. Agent tool calls `get_credential(service_name)`.
2. ApprovalManager creates a pending approval with a unique ID.
3. Telegram bot sends an inline keyboard with [Approve] / [Deny] buttons.
4. User taps a button → callback handler calls `resolve(approval_id, approved)`.
5. The blocked tool call resumes with the approval result.

Thread-safety: the agent runs tools from a background thread, while Telegram
callbacks arrive on the asyncio event loop. We use `threading.Event` to bridge.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# Default timeout for approval requests (5 minutes).
DEFAULT_APPROVAL_TIMEOUT_SECONDS = 300


@dataclass
class PendingApproval:
    """A pending credential-access approval request."""

    approval_id: str
    user_id: str
    service_label: str
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool | None = None


class ApprovalManager:
    """Manages credential-access approval requests.

    The manager holds pending approvals in memory and coordinates between
    the agent's background thread (which blocks on `request_approval`) and
    the Telegram callback handler (which calls `resolve`).
    """

    def __init__(self) -> None:
        self._pending: dict[str, PendingApproval] = {}
        # Cache resolved results so the tool can read them after event.wait().
        # resolve() pops from _pending before event.set(), so the tool needs
        # a separate lookup to determine if the approval was granted or denied.
        self._resolved: dict[str, bool] = {}
        self._lock = threading.Lock()
        # Telegram send callback — set at startup via `set_send_callback`.
        self._send_callback: Callable[..., Awaitable[Any]] | None = None

    def set_send_callback(
        self,
        callback: Callable[[int, str, str], Awaitable[Any]],
    ) -> None:
        """Register the Telegram send function for approval prompts.

        Args:
            callback: Async function `(chat_id, text, approval_id) -> Any`
                that sends an inline-keyboard approval message to the user.
        """
        self._send_callback = callback

    @property
    def send_callback(self) -> Callable[..., Awaitable[Any]] | None:
        return self._send_callback

    def request_approval(
        self,
        user_id: str,
        service_label: str,
    ) -> tuple[str, threading.Event]:
        """Create a pending approval request.

        Called from the agent's background thread. The caller should then
        trigger the Telegram send (on the event loop) and block on the
        returned `threading.Event`.

        Args:
            user_id: User who must approve.
            service_label: Human-readable service name (e.g. "Outlook Work").

        Returns:
            (approval_id, event) — wait on the event for resolution.
        """
        approval_id = str(uuid.uuid4())
        pending = PendingApproval(
            approval_id=approval_id,
            user_id=user_id,
            service_label=service_label,
        )
        with self._lock:
            self._pending[approval_id] = pending

        logger.info(
            "Approval request created: id=%s user=%s service=%s",
            approval_id,
            user_id,
            service_label,
        )
        return approval_id, pending.event

    def resolve(self, approval_id: str, approved: bool) -> bool:
        """Resolve a pending approval.

        Called from the Telegram callback handler when the user taps
        [Approve] or [Deny].

        Args:
            approval_id: The approval request ID.
            approved: True if approved, False if denied.

        Returns:
            True if the approval was found and resolved, False if not found
            (expired or already resolved).
        """
        with self._lock:
            pending = self._pending.pop(approval_id, None)
            if pending is not None:
                # Cache the result before setting the event so the tool can
                # read it after event.wait() returns.
                self._resolved[approval_id] = approved

        if pending is None:
            logger.warning("Approval not found (expired?): %s", approval_id)
            return False

        pending.approved = approved
        pending.event.set()

        logger.info(
            "Approval resolved: id=%s approved=%s user=%s service=%s",
            approval_id,
            approved,
            pending.user_id,
            pending.service_label,
        )
        return True

    def get_result(self, approval_id: str) -> bool | None:
        """Get the result of a resolved approval.

        This is called by the tool after `event.wait()` returns. The result
        is stored in `_resolved` by `resolve()` before the event is set.

        Returns:
            True if approved, False if denied, None if not found.
        """
        with self._lock:
            # Check resolved cache first (approval already completed)
            if approval_id in self._resolved:
                return self._resolved.pop(approval_id)
            # Fall back to pending (shouldn't normally happen)
            pending = self._pending.get(approval_id)
        if pending is None:
            return None
        return pending.approved

    def cleanup_expired(self) -> int:
        """Remove stale pending approvals (best-effort GC).

        Returns:
            Number of approvals removed.
        """
        # In practice, expired approvals are cleaned up when `event.wait()`
        # times out and the caller abandons the request. This method is a
        # safety net for long-running processes.
        with self._lock:
            stale = [
                aid
                for aid, p in self._pending.items()
                if p.event.is_set()
            ]
            for aid in stale:
                self._pending.pop(aid, None)
        return len(stale)
