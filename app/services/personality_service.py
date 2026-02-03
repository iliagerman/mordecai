"""Load personality/identity markdown from the repo-local scratchpad with repo defaults.

Per-user overrides are resolved using a strict layout to prevent cross-user leakage:

- Per-user (Telegram id): users/<USER_ID>/{soul,id}.md

Repo defaults live under `instructions/{soul,id}.md`.

Resolution order (per document): per-user file if it exists, else repo default.

This module is intentionally read-only. Writing is handled by a constrained tool
in app.tools.personality_vault.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PersonalityDocKind = Literal["soul", "id"]
PersonalityDocSource = Literal["user", "repo"]


@dataclass(frozen=True)
class PersonalityDoc:
    kind: PersonalityDocKind
    source: PersonalityDocSource
    path: Path
    content: str


class PersonalityService:
    """Resolve and load personality docs from the scratchpad."""

    def __init__(
        self,
        vault_root: str | None,
        *,
        max_chars: int = 20_000,
        repo_instructions_dir: str | Path | None = None,
    ) -> None:
        self._vault_root_raw = vault_root
        self._max_chars = max_chars
        self._repo_instructions_dir_raw = repo_instructions_dir

    def is_enabled(self) -> bool:
        # Enabled if we can load either per-user vault files or repo defaults.
        return bool(self._vault_root_raw) or bool(self._repo_instructions_dir())

    def _vault_root(self) -> Path | None:
        if not self._vault_root_raw:
            return None
        # Expand ~ for macOS paths.
        return Path(self._vault_root_raw).expanduser().resolve()

    @staticmethod
    def _find_repo_root(*, start: Path) -> Path:
        """Best-effort repository root discovery.

        Mirrors app.config._find_repo_root, but kept local to avoid importing config
        (and any unintended side-effects) from a service module.
        """

        try:
            start = start.resolve()
            for p in [start, *start.parents]:
                if (p / "pyproject.toml").exists():
                    return p
        except Exception:
            pass
        return Path.cwd()

    def _repo_instructions_dir(self) -> Path | None:
        """Return the directory containing repo instruction templates, if present."""

        try:
            raw = self._repo_instructions_dir_raw
            if raw:
                p = Path(str(raw)).expanduser().resolve()
            else:
                repo_root = self._find_repo_root(start=Path(__file__))
                p = (repo_root / "instructions").resolve()

            if p.exists() and p.is_dir():
                return p
        except Exception:
            return None
        return None

    @staticmethod
    def _kind_to_filename(kind: PersonalityDocKind) -> str:
        return "soul.md" if kind == "soul" else "id.md"

    def _resolve_candidate_paths(
        self, user_id: str, kind: PersonalityDocKind
    ) -> list[tuple[PersonalityDocSource, Path]]:
        filename = self._kind_to_filename(kind)

        candidates: list[tuple[PersonalityDocSource, Path]] = []

        root = self._vault_root()
        if root is not None:
            user_path = root / "users" / user_id / filename
            candidates.append(("user", user_path))

        # Built-in defaults live in the repo (instructions/). These are intended to
        # be available even when no Obsidian vault is configured.
        repo_dir = self._repo_instructions_dir()
        if repo_dir is not None:
            candidates.append(("repo", repo_dir / filename))

        return candidates

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
        vault_root = self._vault_root()
        repo_dir = self._repo_instructions_dir()
        if vault_root is None and repo_dir is None:
            return {}

        docs: dict[PersonalityDocKind, PersonalityDoc] = {}
        for kind in ("soul", "id"):
            candidates = self._resolve_candidate_paths(user_id, kind)
            for source, path in candidates:
                # Ensure all reads are constrained to their respective roots.
                if source == "user":
                    if vault_root is None:
                        continue
                    try:
                        safe_path = self._ensure_under_root(vault_root, path)
                    except ValueError:
                        # Should never happen with our fixed layout, but be safe.
                        continue
                else:
                    # repo defaults
                    if repo_dir is None:
                        continue
                    try:
                        safe_path = self._ensure_under_root(repo_dir, path)
                    except ValueError:
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
