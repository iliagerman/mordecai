"""Constrained tools for reading/writing per-user personality files under the scratchpad.

These tools are designed to safely edit ONLY:
- workspace/<USER_ID>/scratchpad/soul.md
- workspace/<USER_ID>/scratchpad/id.md

...and to read built-in defaults from the repo under:
- instructions/soul.md
- instructions/id.md

The scratchpad directory is resolved via ``get_user_scratchpad_path`` and passed
in via ``set_personality_context``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

try:
    from strands import tool  # type: ignore[import-not-found]
except Exception:  # pragma: no cover

    def tool(*_args, **_kwargs):
        def decorator(func):
            return func

        return decorator


PersonalityDocKind = Literal["soul", "id"]
PersonalityDocSource = Literal["auto", "user", "default"]


_scratchpad_dir: str | None = None
_current_user_id: str | None = None
_max_chars: int = 20_000


def set_personality_context(
    scratchpad_dir: str | None,
    user_id: str,
    *,
    max_chars: int = 20_000,
) -> None:
    global _scratchpad_dir, _current_user_id, _max_chars
    _scratchpad_dir = scratchpad_dir
    _current_user_id = user_id
    _max_chars = max_chars


def _require_context() -> tuple[bool, str]:
    if not _scratchpad_dir:
        return (
            False,
            "Scratchpad directory is not configured.",
        )
    if not _current_user_id:
        return False, "User context not available."
    return True, ""


def _resolve_scratchpad() -> Path:
    return Path(_scratchpad_dir).expanduser().resolve()  # type: ignore[arg-type]


def _filename(kind: PersonalityDocKind) -> str:
    return "soul.md" if kind == "soul" else "id.md"


def _find_repo_root(*, start: Path) -> Path:
    """Best-effort repository root discovery (pyproject.toml heuristic)."""

    try:
        start = start.resolve()
        for p in [start, *start.parents]:
            if (p / "pyproject.toml").exists():
                return p
    except Exception:
        pass
    return Path.cwd()


def _repo_default_path(kind: PersonalityDocKind) -> Path:
    repo_root = _find_repo_root(start=Path(__file__))
    return (repo_root / "instructions" / _filename(kind)).resolve()


def _safe_under_root(root: Path, candidate: Path) -> Path:
    resolved = candidate.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except Exception as e:
        raise ValueError(f"Path escapes scratchpad root: {resolved}") from e
    return resolved


def _paths_for(kind: PersonalityDocKind) -> dict[str, Path]:
    root = _resolve_scratchpad()
    fname = _filename(kind)
    user_path = root / fname
    return {
        "user": _safe_under_root(root, user_path),
        "default": _repo_default_path(kind),
    }


def _read_text(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    text = (text or "").strip()
    if not text:
        return None
    if len(text) > _max_chars:
        text = text[:_max_chars].rstrip() + "\n\n[...truncated...]"
    return text


@tool(
    name="personality_read",
    description=(
        "Read the agent's personality/identity file from the configured scratchpad. "
        "Use kind='soul' to read personality instructions or kind='id' to read identity metadata. "
        "Use source='auto' to prefer the per-user file and fallback to default."
    ),
)
def personality_read(
    kind: PersonalityDocKind,
    source: PersonalityDocSource = "auto",
) -> str:
    ok, err = _require_context()
    if not ok:
        return err

    paths = _paths_for(kind)

    if source == "user":
        text = _read_text(paths["user"])
        return text or f"Not found: {paths['user']}"

    if source == "default":
        text = _read_text(paths["default"])
        return text or f"Not found: {paths['default']}"

    # auto
    text = _read_text(paths["user"])
    if text:
        return text
    text = _read_text(paths["default"])
    if text:
        return text
    return f"No {kind}.md found for user {_current_user_id} and no default exists."


@tool(
    name="personality_write",
    description=(
        "Write/update the per-user personality/identity file under the scratchpad. "
        "This ONLY writes to workspace/<USER_ID>/scratchpad/{soul,id}.md. "
        "Use this when the user asks to modify the agent's personality (soul) or identity (id)."
    ),
)
def personality_write(
    kind: PersonalityDocKind,
    content: str,
    *,
    overwrite: bool = True,
) -> str:
    ok, err = _require_context()
    if not ok:
        return err

    content = (content or "").strip()
    if not content:
        return "No content provided."

    paths = _paths_for(kind)
    target = paths["user"]
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and (not overwrite):
        return f"Refusing to overwrite existing file: {target}"

    if len(content) > _max_chars:
        return f"Content too large ({len(content)} chars). Limit is {_max_chars}."

    try:
        target.write_text(content + "\n", encoding="utf-8")
    except Exception as e:
        return f"Failed to write {target}: {e}"

    return f"Saved {kind}.md for user {_current_user_id} at {target}"


@tool(
    name="personality_reset_to_default",
    description=(
        "Reset the per-user personality/identity file to the built-in default version (instructions/{soul,id}.md). "
        "Copies the repo default into the user's scratchpad."
    ),
)
def personality_reset_to_default(kind: PersonalityDocKind) -> str:
    ok, err = _require_context()
    if not ok:
        return err

    paths = _paths_for(kind)
    default_text = _read_text(paths["default"])
    if not default_text:
        return f"Default {kind}.md not found at {paths['default']}"

    target = paths["user"]
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(default_text + "\n", encoding="utf-8")
    except Exception as e:
        return f"Failed to reset {target}: {e}"

    return f"Reset {kind}.md for user {_current_user_id} to default"
