"""Per-conversation memory artifacts in an Obsidian vault.

We persist a *session-level* conversation summary as an Obsidian note:

    me/<USER_ID>/conversations/<SESSION_ID>.md

This is intentionally separate from the rolling STM scratchpad note:

    me/<USER_ID>/stm.md

Rationale:
- STM is a mutable scratchpad (and may be cleared after consolidation).
- Conversation summaries are immutable per-session artifacts.

Security/safety:
- All filesystem operations are constrained under the configured vault root.
- We do NOT store raw transcripts here by default (to avoid accidental secret
  persistence). Only summaries that already passed sensitive checks should be
  written.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

DEFAULT_MAX_CHARS = 20_000


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _vault_root(vault_root_raw: str) -> Path:
    return Path(vault_root_raw).expanduser().resolve()


def _safe_under_root(root: Path, candidate: Path) -> Path:
    resolved = candidate.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except Exception as e:
        raise ValueError(f"Path escapes vault root: {resolved}") from e
    return resolved


def _safe_filename(name: str, *, max_len: int = 120) -> str:
    s = (name or "").strip()
    if not s:
        return "session"
    s = _SAFE_NAME_RE.sub("_", s)
    s = s.strip("._-") or "session"
    if len(s) > max_len:
        s = s[:max_len]
    return s


def conversation_summary_path(vault_root_raw: str, user_id: str, session_id: str) -> Path:
    root = _vault_root(vault_root_raw)
    safe_session = _safe_filename(session_id)
    p = root / "me" / user_id / "conversations" / f"{safe_session}.md"
    return _safe_under_root(root, p)


def write_session_summary(
    vault_root_raw: str,
    user_id: str,
    session_id: str,
    summary: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> Path:
    """Write a session summary note into the Obsidian vault.

    This overwrites any existing note for the same session_id (idempotent).

    Args:
        vault_root_raw: Vault root path.
        user_id: The actor_id/user_id.
        session_id: Session identifier; used as filename (sanitized).
        summary: Summary text (expected to be already vetted for sensitive data).
        max_chars: Maximum file size allowed.

    Returns:
        Path to the created/updated note.
    """
    body = (summary or "").strip()
    if not body:
        raise ValueError("No summary text provided")

    target = conversation_summary_path(vault_root_raw, user_id, session_id)
    target.parent.mkdir(parents=True, exist_ok=True)

    ts = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")

    content = "\n".join(
        [
            "# Conversation summary",
            "",
            f"- created_at: {ts}",
            f"- session_id: {session_id}",
            "",
            body,
            "",
        ]
    )

    if len(content) > max_chars:
        raise ValueError(f"conversation summary note would exceed max_chars={max_chars}")

    target.write_text(content, encoding="utf-8")
    return target
