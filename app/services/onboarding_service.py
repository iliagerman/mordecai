"""Service managing user onboarding flow.

Handles first-time user experience including:
- Loading default personality files from instructions/
- Copying them to user's folder in the Obsidian vault
- Generating onboarding greeting messages
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.services.personality_service import PersonalityDocKind

logger = logging.getLogger(__name__)

# Maximum preview length for each file in the onboarding message.
#
# NOTE: We generally prefer showing the *full* default soul.md/id.md content
# (bounded by max_chars) on first interaction so the user can understand what
# the agent is running with. Keep this constant as a safety valve in case
# these templates grow too large.
PREVIEW_MAX_CHARS = 20_000


class OnboardingService:
    """Service for managing user onboarding flow."""

    def __init__(
        self,
        vault_root: str | None,
        max_chars: int = 20_000,
    ):
        """Initialize onboarding service.

        Args:
            vault_root: Path to the Obsidian vault root.
            max_chars: Maximum characters to read from each personality file.
        """
        self._vault_root_raw = vault_root
        self._max_chars = max_chars

    def _vault_root(self) -> Path | None:
        """Get the vault root path, expanding ~ if present."""
        if not self._vault_root_raw:
            return None
        return Path(self._vault_root_raw).expanduser().resolve()

    @staticmethod
    def _find_repo_root(*, start: Path) -> Path:
        """Best-effort repository root discovery.

        Mirrors app.config._find_repo_root.
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
        """Return the directory containing repo instruction templates."""
        try:
            repo_root = self._find_repo_root(start=Path(__file__))
            p = (repo_root / "instructions").resolve()
            if p.exists() and p.is_dir():
                return p
        except Exception:
            return None
        return None

    @staticmethod
    def _kind_to_filename(kind: PersonalityDocKind) -> str:
        """Convert personality doc kind to filename."""
        return "soul.md" if kind == "soul" else "id.md"

    def _get_default_file_path(self, kind: PersonalityDocKind) -> Path | None:
        """Get the path to a default personality file."""
        repo_dir = self._repo_instructions_dir()
        if repo_dir is None:
            return None
        filename = self._kind_to_filename(kind)
        return repo_dir / filename

    def get_default_soul(self) -> str:
        """Load default soul.md from instructions/.

        Returns:
            Content of the default soul.md file, or empty string if not found.
        """
        path = self._get_default_file_path("soul")
        if path is None or not path.exists():
            return ""
        try:
            content = path.read_text(encoding="utf-8")
            if len(content) > self._max_chars:
                content = content[: self._max_chars].rstrip() + "\n\n[...truncated...]"
            return content
        except Exception as e:
            logger.warning("Failed to read default soul.md: %s", e)
            return ""

    def get_default_id(self) -> str:
        """Load default id.md from instructions/.

        Returns:
            Content of the default id.md file, or empty string if not found.
        """
        path = self._get_default_file_path("id")
        if path is None or not path.exists():
            return ""
        try:
            content = path.read_text(encoding="utf-8")
            if len(content) > self._max_chars:
                content = content[: self._max_chars].rstrip() + "\n\n[...truncated...]"
            return content
        except Exception as e:
            logger.warning("Failed to read default id.md: %s", e)
            return ""

    def _ensure_under_root(self, root: Path, candidate: Path) -> Path:
        """Resolve and ensure candidate path is within root."""
        resolved = candidate.expanduser().resolve()
        try:
            resolved.relative_to(root)
        except Exception as e:
            raise ValueError(f"Path escapes vault root: {resolved}") from e
        return resolved

    async def ensure_user_personality_files(self, user_id: str) -> tuple[bool, str]:
        """Copy default soul.md and id.md to user's folder in vault.

        Args:
            user_id: The user's identifier (Telegram username).

        Returns:
            (success, message) tuple where success is True if files were copied,
            and message contains details or error information.
        """
        vault_root = self._vault_root()
        if vault_root is None:
            return False, "Obsidian vault root is not configured"

        repo_dir = self._repo_instructions_dir()
        if repo_dir is None:
            return False, "Repo instructions directory not found"

        results = []
        user_dir = vault_root / "me" / user_id

        for kind in ("soul", "id"):
            filename = self._kind_to_filename(kind)
            source_path = repo_dir / filename
            target_path = self._ensure_under_root(vault_root, user_dir / filename)

            # Skip if target already exists
            if target_path.exists():
                results.append(f"{filename}: already exists")
                continue

            if not source_path.exists():
                results.append(f"{filename}: source not found, skipping")
                continue

            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                content = source_path.read_text(encoding="utf-8")
                target_path.write_text(content, encoding="utf-8")
                results.append(f"{filename}: created")
                logger.info("Created %s for user %s at %s", filename, user_id, target_path)
            except Exception as e:
                results.append(f"{filename}: failed - {e}")
                logger.error("Failed to create %s for user %s: %s", filename, user_id, e)

        return True, "; ".join(results)

    def _truncate_preview(self, content: str) -> str:
        """Truncate content for preview display."""
        if len(content) <= PREVIEW_MAX_CHARS:
            return content
        return content[:PREVIEW_MAX_CHARS].rstrip() + "\n\n[...truncated for display...]"

    def get_onboarding_message(self, user_id: str) -> str:
        """Return the onboarding greeting message.

        Args:
            user_id: The user's identifier.

        Returns:
            The onboarding message as a string.
        """
        vault_root = self._vault_root()
        vault_note = ""
        if vault_root:
            vault_note = f"📁 me/{user_id}/soul.md\n📁 me/{user_id}/id.md\n\n"
        else:
            vault_note = "Note: Vault not configured, using repo defaults.\n\n"

        soul_content = self.get_default_soul()
        id_content = self.get_default_id()

        # Show the full templates (bounded by max_chars) on first contact so the
        # user can immediately see what drives the agent's behavior.
        soul_preview = self._truncate_preview(soul_content) if soul_content else "*Not found*"
        id_preview = self._truncate_preview(id_content) if id_content else "*Not found*"

        message = (
            f"Welcome to Mordecai! 👋\n\n"
            f"I've set up your personalized personality files in your vault:\n\n"
            f"{vault_note}"
            f"These define how I behave and respond. Here are the defaults:\n\n"
            f"---\n\n"
            f"## soul.md (Personality)\n\n{soul_preview}\n\n"
            f"---\n\n"
            f"## id.md (Identity)\n\n{id_preview}\n\n"
            f"---\n\n"
            f"💡 Want to change how I behave or what I call myself? Just ask.\n"
            f"Tell me what you want to change (tone, verbosity, boundaries, identity metadata, etc.) and I can update:\n"
            f"- `me/{user_id}/soul.md` (personality)\n"
            f"- `me/{user_id}/id.md` (identity)\n\n"
            f"When you're ready, tell me what you want to do next."
        )

        return message

    def get_onboarding_context(self, user_id: str) -> dict[str, str | None] | None:
        """Return the onboarding context for injection into agent prompt.

        This provides the raw soul.md and id.md content so the agent can
        generate its own welcome message instead of us sending a hardcoded one.

        Args:
            user_id: The user's identifier.

        Returns:
            Dict with 'soul' and 'id' keys, or None if onboarding not available.
        """
        soul_content = self.get_default_soul()
        id_content = self.get_default_id()

        if not soul_content and not id_content:
            return None

        return {
            "soul": soul_content,
            "id": id_content,
        }

    def is_enabled(self) -> bool:
        """Check if onboarding service is properly configured."""
        return self._vault_root() is not None or self._repo_instructions_dir() is not None
