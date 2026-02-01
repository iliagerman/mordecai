"""Load personality/identity markdown from an Obsidian vault.

The vault is **outside** the repo workspace. Files are resolved using a strict
layout to prevent cross-user leakage:

- Default (fallback): me/default/{soul,id}.md
- Per-user (Telegram id): me/<USER_ID>/{soul,id}.md

Resolution order (per document): user file if it exists, else default file.

This module is intentionally read-only. Writing is handled by a constrained tool
in app.tools.personality_vault.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PersonalityDocKind = Literal["soul", "id"]
PersonalityDocSource = Literal["user", "default"]


@dataclass(frozen=True)
class PersonalityDoc:
    kind: PersonalityDocKind
    source: PersonalityDocSource
    path: Path
    content: str


class PersonalityService:
    """Resolve and load personality docs from an Obsidian vault."""

    def __init__(
        self,
        vault_root: str | None,
        *,
        max_chars: int = 20_000,
    ) -> None:
        self._vault_root_raw = vault_root
        self._max_chars = max_chars

    def is_enabled(self) -> bool:
        return bool(self._vault_root_raw)

    def _vault_root(self) -> Path | None:
        if not self._vault_root_raw:
            return None
        # Expand ~ for macOS paths.
        return Path(self._vault_root_raw).expanduser().resolve()

    @staticmethod
    def _kind_to_filename(kind: PersonalityDocKind) -> str:
        return "soul.md" if kind == "soul" else "id.md"

    def _resolve_candidate_paths(
        self, user_id: str, kind: PersonalityDocKind
    ) -> list[tuple[PersonalityDocSource, Path]]:
        root = self._vault_root()
        if root is None:
            return []

        filename = self._kind_to_filename(kind)
        user_path = root / "me" / user_id / filename
        default_path = root / "me" / "default" / filename
        return [("user", user_path), ("default", default_path)]

    @staticmethod
    def _ensure_under_root(root: Path, candidate: Path) -> Path:
        """Resolve and ensure candidate path is within root."""
        resolved = candidate.expanduser().resolve()
        try:
            resolved.relative_to(root)
        except Exception as e:
            raise ValueError(f"Path escapes vault root: {resolved}") from e
        return resolved

    def load(self, user_id: str) -> dict[PersonalityDocKind, PersonalityDoc]:
        """Load soul/id docs for a user, with fallback to defaults."""
        root = self._vault_root()
        if root is None:
            return {}

        docs: dict[PersonalityDocKind, PersonalityDoc] = {}
        for kind in ("soul", "id"):
            candidates = self._resolve_candidate_paths(user_id, kind)
            for source, path in candidates:
                try:
                    safe_path = self._ensure_under_root(root, path)
                except ValueError:
                    # Should never happen with our fixed layout, but be safe.
                    continue

                if not safe_path.exists() or not safe_path.is_file():
                    continue

                try:
                    content = safe_path.read_text(encoding="utf-8")
                except Exception:
                    continue

                content = (content or "").strip()
                if not content:
                    continue

                if len(content) > self._max_chars:
                    content = content[: self._max_chars].rstrip() + "\n\n[...truncated...]"

                docs[kind] = PersonalityDoc(
                    kind=kind,
                    source=source,
                    path=safe_path,
                    content=content,
                )
                break

        return docs
