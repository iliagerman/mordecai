"""Short-term memory storage in an Obsidian vault.

We store per-user short-term memories as an Obsidian note at:
    me/<USER_ID>/stm.md

Where <USER_ID> is the same value as actor_id in AgentCore memory.

Backward compatibility:
- Older deployments used: me/<USER_ID>/short_term_memories.md
    We will read from the legacy path if the new note doesn't exist, and we will
    attempt to migrate the legacy file to the new path on first write.

This module is intentionally *not* exposed as Strands tools.
It is an internal system component used by:
- explicit "remember" writes
- daily consolidation cron job

Security/safety:
- All filesystem operations are constrained under the configured vault root.
- The daily consolidation cron is registered as a system task (not DB-backed),
    so it is not editable by users.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import datetime
import re


DEFAULT_MAX_CHARS = 20_000


STM_FILENAME = "stm.md"
LEGACY_STM_FILENAME = "short_term_memories.md"


@dataclass(frozen=True)
class ShortTermMemories:
    facts: list[str]
    preferences: list[str]
    raw_text: str


def _vault_root(vault_root_raw: str) -> Path:
    return Path(vault_root_raw).expanduser().resolve()


def _safe_under_root(root: Path, candidate: Path) -> Path:
    resolved = candidate.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except Exception as e:
        raise ValueError(f"Path escapes vault root: {resolved}") from e
    return resolved


def short_term_memory_path(vault_root_raw: str, user_id: str) -> Path:
    root = _vault_root(vault_root_raw)
    path = root / "me" / user_id / STM_FILENAME
    return _safe_under_root(root, path)


def _legacy_short_term_memory_path(vault_root_raw: str, user_id: str) -> Path:
    root = _vault_root(vault_root_raw)
    path = root / "me" / user_id / LEGACY_STM_FILENAME
    return _safe_under_root(root, path)


def list_user_ids(vault_root_raw: str) -> list[str]:
    """List user ids that exist under <vault>/me/*.

    Excludes the reserved folder 'default'.
    """

    root = _vault_root(vault_root_raw)
    me_dir = root / "me"
    try:
        me_dir = _safe_under_root(root, me_dir)
    except Exception:
        return []

    if not me_dir.exists() or not me_dir.is_dir():
        return []

    user_ids: list[str] = []
    for child in me_dir.iterdir():
        if not child.is_dir():
            continue
        name = child.name
        if name == "default":
            continue
        if name.startswith("."):
            continue
        user_ids.append(name)

    # Deterministic ordering (useful for tests/logging)
    user_ids.sort()
    return user_ids


def append_memory(
    vault_root_raw: str,
    user_id: str,
    *,
    kind: str,
    text: str,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> Path:
    """Append a short-term memory entry for a user.

    Args:
        vault_root_raw: Vault root path.
        user_id: The actor_id/user_id.
        kind: 'fact' or 'preference' (other values treated as 'fact').
        text: Memory text.
        max_chars: Maximum file size allowed; if exceeded, raises ValueError.

    Returns:
        Path to the short-term memory file.
    """

    kind_norm = (kind or "").strip().lower()
    if kind_norm not in {"fact", "preference"}:
        kind_norm = "fact"

    body = (text or "").strip()
    if not body:
        raise ValueError("No short-term memory text provided")

    target = short_term_memory_path(vault_root_raw, user_id)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Migrate legacy file name if it exists (best-effort).
    if not target.exists():
        legacy = _legacy_short_term_memory_path(vault_root_raw, user_id)
        if legacy.exists() and legacy.is_file():
            try:
                legacy.rename(target)
            except Exception:
                # If rename fails (e.g., cross-device), fall back to copy+delete.
                try:
                    target.write_text(legacy.read_text(encoding="utf-8"), encoding="utf-8")
                    legacy.unlink(missing_ok=True)
                except Exception:
                    # If migration fails, we'll just write to the new file.
                    pass

    ts = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")

    entry = f"- [{ts}] ({kind_norm}) {body}\n"

    if target.exists():
        try:
            current_size = target.stat().st_size
        except Exception:
            current_size = 0
        if current_size + len(entry) > max_chars:
            raise ValueError(f"short_term_memories.md would exceed max_chars={max_chars}")
    else:
        header = "# STM\n\n"  # minimal Obsidian-friendly header
        if len(header) + len(entry) > max_chars:
            raise ValueError(f"{STM_FILENAME} would exceed max_chars={max_chars}")
        target.write_text(header, encoding="utf-8")

    with target.open("a", encoding="utf-8") as f:
        f.write(entry)

    return target


def read_raw_text(
    vault_root_raw: str,
    user_id: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str | None:
    """Read the short-term memory file contents (trimmed)."""

    target = short_term_memory_path(vault_root_raw, user_id)
    if not target.exists() or not target.is_file():
        legacy = _legacy_short_term_memory_path(vault_root_raw, user_id)
        if not legacy.exists() or not legacy.is_file():
            return None
        target = legacy

    try:
        text = target.read_text(encoding="utf-8")
    except Exception:
        return None

    text = (text or "").strip()
    if not text:
        return None

    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n\n[...truncated...]"

    return text


_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+\.)\s+")
_TS_PREFIX_RE = re.compile(r"^\[(?P<ts>[^\]]+)\]\s+")
_KIND_PREFIX_RE = re.compile(r"^\((?P<kind>fact|preference)\)\s+", re.IGNORECASE)
_KIND_COLON_RE = re.compile(r"^(?P<kind>fact|preference)\s*:\s+", re.IGNORECASE)


def parse_short_term_memories(raw_text: str) -> ShortTermMemories:
    """Parse short-term memories markdown into facts + preferences.

    We intentionally support a forgiving format:
    - "- [ts] (fact) ..."
    - "- [ts] (preference) ..."
    - "- fact: ..." / "- preference: ..."
    - "- ..." (defaults to fact)

    Non-bullet lines are ignored.
    """

    facts: list[str] = []
    prefs: list[str] = []

    for line in (raw_text or "").splitlines():
        if not _BULLET_RE.match(line):
            continue

        item = _BULLET_RE.sub("", line).strip()
        if not item:
            continue

        # Strip optional timestamp prefix like [2026-01-31T...Z]
        item = _TS_PREFIX_RE.sub("", item).strip()

        kind = "fact"

        m = _KIND_PREFIX_RE.match(item)
        if m:
            kind = m.group("kind").lower()
            item = _KIND_PREFIX_RE.sub("", item).strip()
        else:
            m2 = _KIND_COLON_RE.match(item)
            if m2:
                kind = m2.group("kind").lower()
                item = _KIND_COLON_RE.sub("", item).strip()

        if not item:
            continue

        if kind == "preference":
            prefs.append(item)
        else:
            facts.append(item)

    return ShortTermMemories(facts=facts, preferences=prefs, raw_text=(raw_text or ""))


def read_parsed(
    vault_root_raw: str,
    user_id: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> ShortTermMemories | None:
    text = read_raw_text(vault_root_raw, user_id, max_chars=max_chars)
    if not text:
        return None
    return parse_short_term_memories(text)


def clear(
    vault_root_raw: str,
    user_id: str,
) -> bool:
    """Delete the short-term memory file to start over."""

    targets = [
        short_term_memory_path(vault_root_raw, user_id),
        _legacy_short_term_memory_path(vault_root_raw, user_id),
    ]

    ok = True
    for target in targets:
        if not target.exists():
            continue
        try:
            target.unlink()
        except Exception:
            ok = False
    return ok
