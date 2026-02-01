"""Process health + stall detection.

This module exists to support two operational requirements:
1) Health checks must remain responsive even when the agent is busy.
2) If the agent becomes unresponsive (e.g., a tool hangs), the process should
   become "unhealthy" quickly (for orchestrator restart) and can optionally
   self-terminate.

We keep this module dependency-light so it can be imported from tool wrappers
and FastAPI endpoints.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

from app.models.base import JsonModel


class HealthSnapshot(JsonModel):
    service: str = "mordecai"
    status: str = "healthy"  # healthy | stalled

    inflight: int = 0
    last_progress_age_s: float = 0.0
    last_progress_event: str | None = None


@dataclass(slots=True)
class _HealthState:
    lock: threading.Lock
    inflight: int
    last_progress_monotonic: float
    last_progress_event: str | None


_STATE = _HealthState(
    lock=threading.Lock(),
    inflight=0,
    last_progress_monotonic=time.monotonic(),
    last_progress_event=None,
)


def mark_progress(event: str | None = None) -> None:
    """Update the global progress timestamp.

    Call this at the start/end of long-running operations (agent calls, tools).
    """

    now = time.monotonic()
    with _STATE.lock:
        _STATE.last_progress_monotonic = now
        if event:
            _STATE.last_progress_event = event


def inflight_inc() -> None:
    with _STATE.lock:
        _STATE.inflight += 1
        _STATE.last_progress_monotonic = time.monotonic()
        _STATE.last_progress_event = "inflight.start"


def inflight_dec() -> None:
    with _STATE.lock:
        _STATE.inflight = max(0, _STATE.inflight - 1)
        _STATE.last_progress_monotonic = time.monotonic()
        _STATE.last_progress_event = "inflight.end"


def snapshot(*, stall_seconds: int = 180) -> HealthSnapshot:
    now = time.monotonic()
    with _STATE.lock:
        inflight = int(_STATE.inflight)
        age = float(max(0.0, now - _STATE.last_progress_monotonic))
        last_event = _STATE.last_progress_event

    stalled = inflight > 0 and age >= float(max(1, stall_seconds))

    return HealthSnapshot(
        status="stalled" if stalled else "healthy",
        inflight=inflight,
        last_progress_age_s=age,
        last_progress_event=last_event,
    )


_watchdog_started = False


def start_stall_watchdog(*, stall_seconds: int = 180, enabled: bool = False) -> None:
    """Optionally self-terminate the process if stalled.

    This is a last-resort safety net when an orchestrator restart policy
    cannot be relied upon.

    The watchdog runs in a daemon thread. If it detects a stall, it calls
    os._exit(1) so the process is restarted by an external supervisor.
    """

    global _watchdog_started
    if not enabled:
        return

    if _watchdog_started:
        return

    _watchdog_started = True

    stall_s = int(max(1, stall_seconds))

    def _loop() -> None:
        # Small sleep so we don't spin.
        while True:
            time.sleep(1.0)
            snap = snapshot(stall_seconds=stall_s)
            if snap.status == "stalled":
                # Ensure we emit something to stderr before exiting.
                try:
                    msg = (
                        f"[watchdog] stalled for {snap.last_progress_age_s:.1f}s "
                        f"(inflight={snap.inflight}, last_event={snap.last_progress_event}); "
                        "exiting for restart\n"
                    )
                    os.write(2, msg.encode("utf-8", errors="ignore"))
                except Exception:
                    pass

                os._exit(1)

    t = threading.Thread(target=_loop, name="stall-watchdog", daemon=True)
    t.start()
