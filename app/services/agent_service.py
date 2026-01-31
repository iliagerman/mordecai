"""Agent management service.

This service manages agent instances using Strands SDK with support for
multiple model providers (Bedrock, OpenAI) and session management via
AgentCoreMemorySessionManager for memory persistence.

Each user has their own skills directory, loaded when creating their agent.
Agent name is stored in the database per user, not fetched from memory.

Requirements:
- 1.1: Agent implemented using Strands Agents SDK
- 1.2: Support configurable model providers (Bedrock, OpenAI)
- 1.3: Process user messages through Strands agent loop
- 1.4: Agent has access to file system for reading/writing files
- 1.5: Allow switching between model providers via configuration
- 2.1: Short-term memory maintains conversation context within session
- 2.2: Agent has access to previous messages in session
- 2.3: New command starts fresh session with cleared short-term memory
- 2.4: Session ID is unique per user session
- 7.1: Actor ID derived from user's Telegram username or ID
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.models import BedrockModel
from strands.models.gemini import GeminiModel
from strands.models.openai import OpenAIModel
from strands_tools import file_read, file_write, shell

from app.config import AgentConfig
from app.enums import ModelProvider
from app.services.personality_service import PersonalityService
from app.tools import cron_tools as cron_tools_module
from app.tools import onboard_pending_skills as onboard_pending_skills_module
from app.tools import download_skill as download_skill_module
from app.tools import remember_memory as remember_memory_module
from app.tools import search_memory as search_memory_module
from app.tools import send_file as send_file_module
from app.tools import set_agent_name as set_agent_name_tool
from app.tools import personality_vault as personality_vault_module

if TYPE_CHECKING:
    from strands.models.model import Model

    from app.services.cron_service import CronService
    from app.services.file_service import FileService
    from app.services.memory_extraction_service import MemoryExtractionService
    from app.services.memory_service import MemoryService
    from app.services.pending_skill_service import PendingSkillService

logger = logging.getLogger(__name__)


# Directories inside skills/ that are not actual skills
_RESERVED_SKILL_DIR_NAMES = {
    "pending",
    "failed",
    ".venvs",
    ".venv",
    "__pycache__",
}


def _parse_skill_frontmatter(content: str) -> dict:
    """Parse YAML frontmatter from SKILL.md content."""
    if not content.startswith("---"):
        return {}

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}

    frontmatter = {}
    for line in parts[1].strip().split("\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if value.startswith("-") or not value:
                continue
            frontmatter[key] = value

    return frontmatter


class AgentService:
    """Agent management service with per-user skills and AgentCore memory.

    Each user has their own:
    - Skills directory at {skills_base_dir}/{user_id}/
    - Conversation history tracked in-memory for extraction
    - Unique session_id for conversation tracking
    - Agent name stored in database (not fetched from memory)

    Memory Architecture:
    - Session memory: SlidingWindowConversationManager (in-memory, per-session)
    - Long-term memory: AgentCore Memory (facts, preferences, summaries)
    """

    def __init__(
        self,
        config: AgentConfig,
        memory_service: "MemoryService | None" = None,
        cron_service: "CronService | None" = None,
        skill_service=None,
        extraction_service: "MemoryExtractionService | None" = None,
        file_service: "FileService | None" = None,
        pending_skill_service: "PendingSkillService | None" = None,
    ) -> None:
        """Initialize the agent service.

        Args:
            config: Application configuration.
            memory_service: Optional MemoryService for AgentCore memory.
            cron_service: Optional CronService for scheduled task management.
            extraction_service: Optional MemoryExtractionService for
                memory extraction.
            file_service: Optional FileService for file operations.
        """
        self.config = config
        self.memory_service = memory_service
        self.cron_service = cron_service
        self.extraction_service = extraction_service
        self.file_service = file_service
        self.pending_skill_service = pending_skill_service
        self.skill_service = skill_service
        self._user_sessions: dict[str, str] = {}
        self._user_agent_names: dict[str, str | None] = {}  # cached names
        self._conversation_history: dict[str, list[dict]] = {}  # extraction
        self._user_message_counts: dict[str, int] = {}  # per-user counts
        self._extraction_in_progress: dict[str, bool] = {}  # track extractions
        self._skills_base_dir = Path(config.skills_base_dir)
        self._skills_base_dir.mkdir(parents=True, exist_ok=True)
        self._user_agents: dict[str, Agent] = {}  # cached agent instances
        self._user_conversation_managers: dict[
            str, SlidingWindowConversationManager
        ] = {}  # cached managers

        # External personality/identity loader (Obsidian vault)
        self.personality_service = PersonalityService(
            config.obsidian_vault_root,
            max_chars=getattr(config, "personality_max_chars", 20_000),
        )

    def _build_personality_section(self, user_id: str) -> str:
        """Build system-prompt sections for personality (soul) + identity (id).

        Files are loaded from the configured Obsidian vault root:
          - me/<TELEGRAM_ID>/soul.md, me/<TELEGRAM_ID>/id.md
          - fallback: me/default/soul.md, me/default/id.md
        """

        if not getattr(self.config, "personality_enabled", True):
            return ""
        if not self.personality_service.is_enabled():
            return ""

        docs = self.personality_service.load(user_id)
        if not docs:
            return ""

        lines: list[str] = []
        lines.append("## Personality (Obsidian Vault)\n")
        lines.append(
            "The following files are loaded from the configured Obsidian vault and must be followed as system-level instructions.\n"
        )

        if "soul" in docs:
            soul = docs["soul"]
            lines.append(f"### soul.md (source: {soul.source})\n")
            lines.append(f"Path: `{soul.path}`\n")
            lines.append(soul.content)
            lines.append("")

        if "id" in docs:
            ident = docs["id"]
            lines.append(f"### id.md (source: {ident.source})\n")
            lines.append(f"Path: `{ident.path}`\n")
            lines.append(ident.content)
            lines.append("")

        lines.append("")
        return "\n".join(lines)

    def _get_user_skills_dir(self, user_id: str) -> Path:
        """Get the skills directory for a specific user.

        Also syncs shared skills into the user's directory so Strands can
        load all tools from a single directory.

        Current behavior: shared skills are mirrored into the user directory
        on every call, overwriting any existing same-named entries. This keeps
        the per-user tools directory always in sync with shared skills.
        """
        user_dir = self._skills_base_dir / user_id
        user_dir.mkdir(parents=True, exist_ok=True)
        self._sync_shared_skills(user_dir)
        return user_dir

    def _sync_shared_skills_for_user(self, user_id: str) -> Path:
        """Sync shared skills into the user's skills directory.

        This is an explicit helper so we can guarantee sync happens at the
        start of every message-processing path (even if agent caching/reuse
        changes in the future).

        Returns:
            The user's skills directory path.
        """
        return self._get_user_skills_dir(user_id)

    def _sync_shared_skills(self, user_dir: Path) -> None:
        """Mirror shared skills into a user's directory.

        This is intended to run on each message to ensure the user's skills
        directory reflects the latest shared skills.

        Semantics:
        - For each shared skill (file or directory), copy into user_dir.
        - If the destination exists, it is overwritten.
        - If a previously-synced shared skill was deleted from shared/, remove
          the corresponding entry from user_dir.

        To avoid expensive full copies on every call, we keep a lightweight
        per-user manifest with fingerprints of each synced shared entry.

        Args:
            user_dir: User's skills directory.
        """
        import shutil

        shared_dir = Path(self.config.shared_skills_dir)
        if not shared_dir.exists():
            logger.debug("Shared skills dir does not exist: %s", shared_dir)
            return

        manifest_path = user_dir / ".shared_skills_sync.json"

        def load_manifest() -> dict:
            try:
                if manifest_path.exists():
                    return json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                pass
            return {"synced": {}}

        def save_manifest(data: dict) -> None:
            try:
                tmp = manifest_path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
                tmp.replace(manifest_path)
            except Exception as e:
                logger.debug("Failed to write shared skills manifest: %s", e)

        def fingerprint_path(p: Path) -> dict:
            """Compute a best-effort fingerprint for p.

            For directories, we hash only metadata (mtime_ns + size) of contained
            files, not file contents.
            """
            try:
                if p.is_file():
                    st = p.stat()
                    return {
                        "kind": "file",
                        "mtime_ns": st.st_mtime_ns,
                        "size": st.st_size,
                    }
                if p.is_dir():
                    max_mtime_ns = 0
                    total_size = 0
                    file_count = 0
                    for root, _dirs, files in os.walk(p):
                        for fn in files:
                            fp = Path(root) / fn
                            try:
                                st = fp.stat()
                            except OSError:
                                continue
                            file_count += 1
                            total_size += int(st.st_size)
                            max_mtime_ns = max(max_mtime_ns, int(st.st_mtime_ns))
                    return {
                        "kind": "dir",
                        "max_mtime_ns": max_mtime_ns,
                        "total_size": total_size,
                        "file_count": file_count,
                    }
            except Exception:
                pass

            return {"kind": "unknown"}

        manifest = load_manifest()
        synced: dict[str, dict] = dict(manifest.get("synced", {}))

        # Determine which shared entries should be mirrored.
        shared_items: dict[str, Path] = {}
        for item in shared_dir.iterdir():
            # Skip private/dunder entries and non-skill reserved dirs.
            if item.name.startswith("__"):
                continue
            if item.is_file() and item.name == "__init__.py":
                continue
            if item.is_dir() and item.name in _RESERVED_SKILL_DIR_NAMES:
                continue

            shared_items[item.name] = item

        removed: list[str] = []
        updated: list[str] = []

        # Remove entries that were previously synced but no longer exist in shared.
        for name in list(synced.keys()):
            if name in shared_items:
                continue
            dest = user_dir / name
            if dest.exists():
                try:
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                    removed.append(name)
                except Exception as e:
                    logger.warning("Failed to remove stale shared skill %s: %s", name, e)
            synced.pop(name, None)

        # Mirror/overwrite current shared skills.
        for name, src in shared_items.items():
            dest = user_dir / name
            fp = fingerprint_path(src)
            prev_fp = (synced.get(name) or {}).get("fingerprint")

            # Skip if unchanged and destination exists.
            if prev_fp == fp and dest.exists():
                continue

            # Overwrite destination.
            if dest.exists():
                try:
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                except Exception as e:
                    logger.warning(
                        "Failed to remove existing dest for shared skill %s: %s",
                        name,
                        e,
                    )
                    # If we can't remove it, don't attempt to copy over it.
                    continue

            try:
                if src.is_dir():
                    shutil.copytree(src, dest)
                else:
                    shutil.copy2(src, dest)
                synced[name] = {
                    "fingerprint": fp,
                    "synced_at": datetime.utcnow().isoformat(),
                    "source": str(src),
                }
                updated.append(name)
            except Exception as e:
                logger.warning("Failed to sync shared skill %s: %s", name, e)

        if removed or updated:
            manifest["synced"] = synced
            save_manifest(manifest)

        if removed:
            logger.info("Removed stale shared skills for user: %s", ", ".join(removed))
        if updated:
            logger.info("Synced/updated shared skills for user: %s", ", ".join(updated))

    def _get_session_id(self, user_id: str) -> str:
        """Get or create a session ID for a user."""
        if user_id not in self._user_sessions:
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            self._user_sessions[user_id] = f"session_{user_id}_{timestamp}"
        return self._user_sessions[user_id]

    def increment_message_count(self, user_id: str, count: int = 1) -> int:
        """Increment and return the message count for a user.

        Args:
            user_id: User's telegram ID.
            count: Number to increment by (default 1).

        Returns:
            Updated message count for the user.
        """
        current = self._user_message_counts.get(user_id, 0)
        self._user_message_counts[user_id] = current + count
        return self._user_message_counts[user_id]

    def get_message_count(self, user_id: str) -> int:
        """Get current message count for a user.

        Args:
            user_id: User's telegram ID.

        Returns:
            Current message count (0 if user has no messages).
        """
        return self._user_message_counts.get(user_id, 0)

    def reset_message_count(self, user_id: str) -> None:
        """Reset message count to zero for a user.

        Args:
            user_id: User's telegram ID.
        """
        self._user_message_counts[user_id] = 0

    def _create_model(self, use_vision: bool = False) -> "Model":
        """Create model instance based on configured provider.

        Args:
            use_vision: If True and vision_model_id is configured,
                use the vision model instead of the default model.

        Returns:
            Model instance for inference.

        Requirements:
            - 3.1: Use vision model when configured and processing images
            - 3.2: Detect vision capability based on vision_model_id setting
            - 3.3: Fall back to default model if no vision model configured
            - 3.5: Support configuring separate vision model
        """
        # Use vision model if requested and configured (Req 3.1, 3.2, 3.5)
        if use_vision and self.config.vision_model_id:
            if self.config.bedrock_api_key:
                os.environ["AWS_BEARER_TOKEN_BEDROCK"] = self.config.bedrock_api_key
            return BedrockModel(
                model_id=self.config.vision_model_id,
                region_name=self.config.aws_region,
            )

        # Fall back to default model (Req 3.3)
        match self.config.model_provider:
            case ModelProvider.BEDROCK:
                model_id = self.config.bedrock_model_id
                if self.config.bedrock_api_key:
                    os.environ["AWS_BEARER_TOKEN_BEDROCK"] = self.config.bedrock_api_key
                return BedrockModel(
                    model_id=model_id,
                    region_name=self.config.aws_region,
                )
            case ModelProvider.OPENAI:
                if not self.config.openai_api_key:
                    raise ValueError("OpenAI API key required")
                return OpenAIModel(
                    model=self.config.openai_model_id,
                    api_key=self.config.openai_api_key,
                )
            case ModelProvider.GOOGLE:
                if not self.config.google_api_key:
                    raise ValueError("Google API key required")
                return GeminiModel(
                    client_args={"api_key": self.config.google_api_key},
                    model_id=self.config.google_model_id,
                    params={"max_output_tokens": 4096},
                )
            case _:
                raise ValueError(f"Unknown model provider: {self.config.model_provider}")

    def set_agent_name(self, user_id: str, name: str) -> None:
        """Set the agent name for a user (cached, caller saves to DB).

        Args:
            user_id: User's telegram ID.
            name: Name to assign to the agent.
        """
        self._user_agent_names[user_id] = name
        logger.info("Set agent name for user %s: %s", user_id, name)

    def _on_agent_name_changed(self, user_id: str, name: str) -> None:
        """Callback when agent name is changed via tool.

        Updates the in-memory cache so the current session uses the new name.

        Args:
            user_id: User's telegram ID.
            name: New name for the agent.
        """
        self._user_agent_names[user_id] = name
        logger.info("Agent name changed via tool for user %s: %s", user_id, name)

    def get_agent_name(self, user_id: str) -> str | None:
        """Get the cached agent name for a user.

        Args:
            user_id: User's telegram ID.

        Returns:
            Agent name if set, None otherwise.
        """
        return self._user_agent_names.get(user_id)

    def _build_commands_section(self) -> str:
        """Build commands section from config."""
        commands = self.config.agent_commands
        if not commands:
            return ""

        lines = ["## Available Commands\n"]
        for cmd in commands:
            name = cmd.get("name", "")
            desc = cmd.get("description", "")
            lines.append(f"- {name}: {desc}")
        lines.append("")
        return "\n".join(lines)

    def _get_user_working_dir(self, user_id: str) -> Path:
        """Get or create user's working directory.

        Args:
            user_id: User's telegram ID.

        Returns:
            Path to user's working directory.

        Requirements:
            - 10.1: Create user-specific working folder
            - 10.2: Agent has read/write access to working folder
        """
        if self.file_service is not None:
            return self.file_service.get_user_working_dir(user_id)
        # Fallback if no file service
        work_dir = Path(self.config.working_folder_base_dir) / user_id
        work_dir.mkdir(parents=True, exist_ok=True)
        return work_dir

    def _build_attachment_context(
        self,
        attachments: list[dict] | None,
        user_id: str,
    ) -> str:
        """Build context string for file attachments.

        Copies files from temp directory to user's workspace if needed,
        so the agent works with files in the correct location.

        Args:
            attachments: List of attachment metadata dicts.
            user_id: User ID for workspace directory.

        Returns:
            Formatted string with attachment information.

        Requirements:
            - 4.2: Provide agent with full path to downloaded files
            - 4.3: Include file metadata in agent's context
            - 4.5: Inform agent of file type and location
        """
        if not attachments:
            return ""

        # Get user's workspace directory
        workspace_dir = self._get_user_working_dir(user_id)

        lines = ["\n## Received Files\n"]
        for att in attachments:
            file_name = att.get("file_name", "unknown")
            mime_type = att.get("mime_type", "unknown")
            file_path = att.get("file_path", "")
            file_size = att.get("file_size", 0)

            # Copy file to workspace if it's in temp directory
            if file_path:
                src_path = Path(file_path)
                if src_path.exists():
                    dest_path = workspace_dir / file_name
                    # Only copy if not already in workspace
                    if src_path.parent != workspace_dir:
                        import shutil

                        shutil.copy2(src_path, dest_path)
                        file_path = str(dest_path)
                        logger.info("Copied attachment %s to workspace: %s", file_name, dest_path)

            # Format size for readability
            if file_size >= 1024 * 1024:
                size_str = f"{file_size / (1024 * 1024):.1f}MB"
            elif file_size >= 1024:
                size_str = f"{file_size / 1024:.1f}KB"
            else:
                size_str = f"{file_size} bytes"

            lines.append(f"- **{file_name}** ({mime_type}, {size_str})")
            lines.append(f"  Path: `{file_path}`")

        lines.append("")
        return "\n".join(lines)

    def _build_system_prompt(
        self,
        user_id: str,
        memory_context: dict | None = None,
        attachments: list[dict] | None = None,
    ) -> str:
        """Build the system prompt with agent name, memory, and working folder.

        Args:
            user_id: User's telegram ID.
            memory_context: Retrieved memory context with facts, preferences.
            attachments: List of file attachment metadata.

        Returns:
            System prompt string.

        Requirements:
            - 10.2: Agent has read/write access to working folder
            - 10.3: Provide working folder path in system prompt
        """
        memory_context = memory_context or {}
        # Check cached name first, then fall back to memory context
        agent_name = self._user_agent_names.get(user_id)
        if not agent_name:
            agent_name = memory_context.get("agent_name")
            # Cache it for future use in this session
            if agent_name:
                self._user_agent_names[user_id] = agent_name
                logger.info("Loaded agent name '%s' from memory for user %s", agent_name, user_id)
        facts = memory_context.get("facts", [])
        preferences = memory_context.get("preferences", [])

        # Identity section
        if agent_name:
            identity = (
                f"YOUR NAME IS {agent_name.upper()}.\n"
                f"Always identify yourself as {agent_name}.\n"
                f"When asked your name, say: 'I'm {agent_name}.'\n"
                "NEVER say you are Claude, ChatGPT, or any generic AI.\n"
            )
        else:
            # Check if memories mention a name - tell agent to look there
            has_name_in_memory = any(
                "assistant" in str(f).lower()
                and any(word in str(f).lower() for word in ["call", "name", "mordecai", "jarvis"])
                for f in facts + preferences
            )
            if has_name_in_memory:
                identity = (
                    "Check the Retrieved Memory section below - it may "
                    "contain your name that the user previously gave you.\n"
                    "If you find your name there, use it to identify "
                    "yourself.\n"
                    "NEVER say you are Claude, ChatGPT, or any generic AI.\n"
                )
            else:
                identity = (
                    "You do not have a name yet.\n"
                    "When asked your name, respond:\n"
                    "'I don't have a name yet. What would you like to "
                    "call me?'\n"
                    "NEVER say you are Claude, ChatGPT, or any generic AI.\n"
                    "When a user gives you a name, use the set_agent_name "
                    "tool to store it in memory so you remember it across "
                    "sessions.\n"
                    "IMPORTANT: If the set_agent_name tool returns an error, "
                    "do NOT claim you will remember the name. Be honest that "
                    "memory storage failed and you can only use the name for "
                    "this session.\n"
                )

        # Get current date/time in configured timezone
        try:
            tz = ZoneInfo(self.config.timezone)
        except Exception:
            tz = ZoneInfo("UTC")
        now = datetime.now(tz)
        current_datetime = now.strftime("%A, %B %d, %Y at %I:%M %p %Z")

        prompt = (
            "You are a helpful AI assistant with access to tools.\n\n"
            f"## Current Date and Time\n\n{current_datetime}\n\n"
            f"## Identity\n\n{identity}\n"
        )

        # External personality/identity injection (Obsidian vault)
        prompt += self._build_personality_section(user_id)

        # Memory capabilities section (when memory is enabled)
        if self.config.memory_enabled:
            prompt += (
                "## Memory Capabilities\n\n"
                "You have two types of memory:\n\n"
                "1. **Session Memory**: Conversation history within this "
                "session (automatically managed).\n\n"
                "2. **Long-Term Memory**: Facts and preferences about the "
                "user that persist across sessions.\n\n"
                "**Tools:**\n"
                "- `set_agent_name`: Store your name when the user gives "
                "you one.\n"
                "- `remember_fact`: Store an explicit fact the user asked you to remember.\n"
                "- `remember_preference`: Store an explicit preference the user asked you to remember.\n"
                "- `remember`: Convenience wrapper (fact vs preference).\n"
                "- `search_memory`: Search your long-term memory when the "
                "user asks about past conversations, their preferences, "
                "or facts you've learned about them.\n\n"
                "**Important:** When the user explicitly says 'remember ...' or 'please remember ...', "
                "store it immediately using `remember_fact` or `remember_preference`.\n\n"
                "Use `search_memory` when the user asks things like:\n"
                "- 'What do you know about me?'\n"
                "- 'What are my preferences?'\n"
                "- 'Do you remember when I told you...?'\n"
                "- 'What have we discussed before?'\n\n"
            )

        # Memory context section - show relevant memories retrieved for
        # this conversation
        if facts or preferences:
            prompt += "## Retrieved Memory\n\n"
            prompt += (
                "The following information was retrieved from your "
                "long-term memory based on the current conversation:\n\n"
            )
            if facts:
                prompt += "**Facts about the user:**\n"
                for fact in facts[:5]:
                    prompt += f"- {fact}\n"
                prompt += "\n"
            if preferences:
                prompt += "**User preferences:**\n"
                for pref in preferences[:5]:
                    prompt += f"- {pref}\n"
                prompt += "\n"

        # Commands from config
        prompt += self._build_commands_section()

        # Skills - instruction-based plugins
        skills_info = self._discover_skills(user_id)
        if skills_info:
            prompt += "\n## Installed Skills\n\n"
            prompt += (
                "Skills contain instructions with bash commands to execute.\n\n"
                "**Your available tools:**\n"
                '- `shell(command="...")` - Run bash/shell commands\n'
                '- `file_read(path="...", mode="view")` - Read files\n'
                "- `file_write(...)` - Write files\n\n"
                "**To use a skill:**\n"
                "1. file_read the SKILL.md ONCE\n"
                "2. Extract the bash commands from the instructions\n"
                '3. Run them with shell(command="the bash command")\n\n'
                "**CRITICAL:** After reading SKILL.md, your next tool call "
                "MUST be shell(), not file_read. Skills say 'Bash' but use "
                "shell() tool.\n\n"
            )
            for skill in skills_info:
                name = skill.get("name", "unknown")
                desc = skill.get("description", "")
                path = skill.get("path", "")
                prompt += f"- **{name}**: {desc}\n"
                prompt += (
                    f'  → file_read(path="{path}/SKILL.md", mode="view") → shell(command="...")\n'
                )

        # Working folder section (Requirement 10.3)
        working_dir = self._get_user_working_dir(user_id)
        wd = working_dir  # Short alias for examples
        prompt += "\n## Working Folder\n\n"
        prompt += f"**Your working folder is: `{working_dir}`**\n\n"
        prompt += (
            "**CRITICAL: Use this folder for ALL file operations:**\n"
            f"- When creating files, use full path: `{wd}/<filename>`\n"
            f"- When saving output, save to: `{wd}/<filename>`\n"
            "- NEVER create files in `.` or any other location\n"
            "- NEVER use relative paths without the working folder prefix\n\n"
            "**CRITICAL: For shell commands, ALWAYS set work_dir:**\n"
            f'- shell(command="...", work_dir="{wd}")\n'
            "- This ensures commands run in your working folder\n"
            "- Output files will be saved in the correct location\n\n"
            "Examples:\n"
            f'- ✅ Correct: `file_write(path="{wd}/out.txt", ...)`\n'
            f'- ✅ Correct: `file_write(path="{wd}/data.json", ...)`\n'
            f'- ✅ Correct: `shell(command="uv run ...", '
            f'work_dir="{wd}")`\n'
            '- ❌ Wrong: `file_write(path="output.txt", ...)`\n'
            '- ❌ Wrong: `file_write(path="./data.json", ...)`\n'
            '- ❌ Wrong: `shell(command="...")` (missing work_dir)\n\n'
            "You have read and write access to this directory.\n"
        )

        # Cron/Scheduling capabilities section
        if self.cron_service is not None:
            prompt += "\n## Scheduling Capabilities\n\n"
            prompt += (
                "You can create, list, and delete scheduled tasks using "
                "the provided tools. These are YOUR tools - do NOT use "
                "system crontab or shell commands for scheduling.\n\n"
                "**CRITICAL: Use ONLY these tools for scheduling:**\n"
                "- `create_cron_task(name, instructions, cron_expression)`: "
                "Create a scheduled task\n"
                "- `list_cron_tasks()`: Show all scheduled tasks\n"
                "- `delete_cron_task(task_identifier)`: Remove a task\n\n"
                "**How it works:**\n"
                "When a scheduled task fires, YOU (the agent) will receive "
                "the `instructions` as a message and respond to the user. "
                "The instructions should describe what YOU should do, not "
                "shell commands.\n\n"
                "**Example - User wants a joke every 5 minutes:**\n"
                "```\n"
                "create_cron_task(\n"
                '  name="joke-sender",\n'
                '  instructions="Tell the user a funny joke",\n'
                '  cron_expression="*/5 * * * *"\n'
                ")\n"
                "```\n"
                "When this fires, you'll be asked to tell a joke and you'll "
                "generate one naturally.\n\n"
                "**Example - Daily weather update:**\n"
                "```\n"
                "create_cron_task(\n"
                '  name="weather-update",\n'
                '  instructions="Check the weather and send a summary",\n'
                '  cron_expression="0 8 * * *"\n'
                ")\n"
                "```\n\n"
                "**DO NOT:**\n"
                "- Use shell() to run crontab commands\n"
                "- Create files to store content for cron jobs\n"
                "- Try to schedule shell scripts\n\n"
                "**Cron Expression Format (5 fields):**\n"
                "minute hour day month weekday\n"
                "- `*/5 * * * *` = Every 5 minutes\n"
                "- `0 6 * * *` = Daily at 6:00 AM\n"
                "- `0 9 * * 1-5` = Weekdays at 9:00 AM\n"
                "- `0 */2 * * *` = Every 2 hours\n\n"
            )

        # Attachment context (Requirements 4.2, 4.3, 4.5)
        prompt += self._build_attachment_context(attachments, user_id)

        return prompt

    def _discover_skills(self, user_id: str) -> list[dict]:
        """Discover installed instruction-based skills for a user.

        Skills are instruction-based: they contain a SKILL.md file with
        step-by-step instructions that the agent must read and follow.

        Shared skills are mirrored into the per-user skills directory on each
        message. Therefore, the single source of truth for what the agent can
        load is the user's directory.
        """
        skills_by_name: dict[str, dict] = {}

        # Ensure shared skills are synced before discovery.
        user_skills_dir = self._get_user_skills_dir(user_id)

        if user_skills_dir.exists():
            for item in user_skills_dir.iterdir():
                if not item.is_dir() or item.name.startswith("__"):
                    continue

                if item.name in _RESERVED_SKILL_DIR_NAMES:
                    continue

                skill_md = item / "SKILL.md"
                if not skill_md.exists():
                    continue

                try:
                    content = skill_md.read_text(encoding="utf-8")
                    frontmatter = _parse_skill_frontmatter(content)
                    skill_name = frontmatter.get("name", item.name)
                    skills_by_name[skill_name] = {
                        "name": skill_name,
                        "description": frontmatter.get("description", ""),
                        "path": str(item.resolve()),
                    }
                except Exception as e:
                    logger.warning("Failed to read skill %s: %s", item, e)

        return list(skills_by_name.values())

    def _get_or_create_conversation_manager(self, user_id: str) -> SlidingWindowConversationManager:
        """Get or create a conversation manager for a user.

        This ensures the same conversation manager is reused across messages
        so the agent maintains conversation history within a session.

        Args:
            user_id: User's telegram ID.

        Returns:
            SlidingWindowConversationManager instance for the user.
        """
        if user_id not in self._user_conversation_managers:
            self._user_conversation_managers[user_id] = SlidingWindowConversationManager(
                window_size=self.config.conversation_window_size,
            )
        return self._user_conversation_managers[user_id]

    def _get_user_messages(self, user_id: str) -> list:
        """Get the cached messages for a user's session.

        Args:
            user_id: User's telegram ID.

        Returns:
            List of messages from the user's current session.
        """
        # Get messages from cached agent if it exists
        if user_id in self._user_agents:
            return self._user_agents[user_id].messages
        return []

    def _cache_agent(self, user_id: str, agent: Agent) -> None:
        """Cache an agent instance for a user.

        Args:
            user_id: User's telegram ID.
            agent: Agent instance to cache.
        """
        self._user_agents[user_id] = agent

    def _create_agent(
        self,
        user_id: str,
        memory_context: dict | None = None,
        attachments: list[dict] | None = None,
        messages: list | None = None,
    ) -> Agent:
        """Create an agent instance.

        Args:
            user_id: User's telegram ID.
            memory_context: Retrieved memory context.
            attachments: List of file attachment metadata.
            messages: Previous conversation messages to restore context.

        Returns:
            Configured Agent instance.
        """
        model = self._create_model()
        user_skills_dir = str(self._get_user_skills_dir(user_id))

        # Get or create conversation manager for this user
        # This ensures conversation history is preserved across messages
        conversation_manager = self._get_or_create_conversation_manager(user_id)

        # Set up the set_agent_name tool with memory service context
        if self.memory_service is not None:
            session_id = self._get_session_id(user_id)
            set_agent_name_tool.set_memory_service(
                self.memory_service,
                user_id,
                session_id,
                on_name_changed=self._on_agent_name_changed,
            )
            # Set up search_memory tool context
            search_memory_module.set_memory_context(self.memory_service, user_id)
            # Set up explicit remember tools context
            remember_memory_module.set_memory_context(
                self.memory_service,
                user_id,
                session_id,
            )

        # Set up cron tools with cron service context
        if self.cron_service is not None:
            cron_tools_module.set_cron_context(
                self.cron_service,
                user_id,
            )

        # Set up pending skill tools with pending skill service context
        if self.pending_skill_service is not None:
            onboard_pending_skills_module.set_pending_skill_context(
                self.pending_skill_service,
                user_id,
            )

        # Set up pending skill download tool with skill service context
        if self.skill_service is not None:
            download_skill_module.set_skill_download_context(
                self.skill_service,
                user_id,
            )

        # Set up personality vault tools context (Obsidian vault)
        personality_vault_module.set_personality_context(
            getattr(self.config, "obsidian_vault_root", None),
            user_id,
            max_chars=getattr(self.config, "personality_max_chars", 20_000),
        )

        # Include tools for memory and file operations
        tools = [
            shell,
            file_read,
            file_write,
            set_agent_name_tool,
            send_file_module,
        ]

        # Add personality vault tools (read/write soul.md + id.md under me/<TELEGRAM_ID>/)
        tools.extend(
            [
                personality_vault_module.personality_read,
                personality_vault_module.personality_write,
                personality_vault_module.personality_reset_to_default,
            ]
        )

        # Add search_memory tool if memory service is available
        if self.memory_service is not None:
            tools.append(search_memory_module.search_memory)
            tools.extend(
                [
                    remember_memory_module.remember_fact,
                    remember_memory_module.remember_preference,
                    remember_memory_module.remember,
                ]
            )

        # Add cron tools if cron service is available
        if self.cron_service is not None:
            tools.extend(
                [
                    cron_tools_module.create_cron_task,
                    cron_tools_module.list_cron_tasks,
                    cron_tools_module.delete_cron_task,
                ]
            )

        # Add pending skill onboarding tools if service is available
        if self.pending_skill_service is not None:
            tools.extend(
                [
                    onboard_pending_skills_module.list_pending_skills,
                    onboard_pending_skills_module.onboard_pending_skills,
                    onboard_pending_skills_module.repair_skill_dependencies,
                ]
            )

        # Add skill download tool if skill service is available
        if self.skill_service is not None:
            tools.append(download_skill_module.download_skill_to_pending)

        agent = Agent(
            model=model,
            messages=messages,
            conversation_manager=conversation_manager,
            tools=tools,
            load_tools_from_directory=user_skills_dir,
            system_prompt=self._build_system_prompt(user_id, memory_context, attachments),
        )

        # Log loaded skills for this user
        logger.info(
            "User %s: Skills dir=%s, shared_dir=%s",
            user_id,
            user_skills_dir,
            self.config.shared_skills_dir,
        )
        skills = self._discover_skills(user_id)
        if skills:
            skill_names = [s["name"] for s in skills]
            logger.info(
                "User %s: Loaded %d skills: %s", user_id, len(skills), ", ".join(skill_names)
            )
        else:
            logger.info("User %s: No skills loaded", user_id)

        # Cache the agent so we can retrieve messages later
        self._cache_agent(user_id, agent)

        return agent

    def get_or_create_agent(self, user_id: str) -> Agent:
        """Create an agent for user."""
        return self._create_agent(user_id)

    async def new_session(self, user_id: str) -> tuple[Agent, str]:
        """Create a fresh session with extraction before clearing.

        Triggers memory extraction before clearing the session to preserve
        important information. Returns both the new agent and a user message.

        Implements graceful degradation (Requirements 6.1, 6.2, 6.4):
        - Logs errors and proceeds with clearing if extraction fails
        - Skips extraction if memory service is unavailable
        - Uses asyncio.wait_for with configured timeout
        - Proceeds with session clearing after timeout

        Args:
            user_id: User's telegram ID.

        Returns:
            Tuple of (new Agent instance, user notification message).

        Requirements:
            - 4.1: Invoke MemoryExtractionService before clearing session
            - 4.2: Wait for extraction to complete (with timeout)
            - 4.3: Handle extraction failures gracefully
            - 4.4: Inform user that conversation was analyzed
        """
        logger.info("Creating new session for user_id=%s", user_id)

        extraction_success = False
        summary_text: str | None = None
        msg_count = self.get_message_count(user_id)

        # Trigger extraction if we have messages and services available
        # (Requirement 6.2: Skip if memory service unavailable)
        if self.extraction_service and self.memory_service and msg_count > 0:
            try:
                # Ensure we have a session_id even if the user triggers /new
                # very early.
                session_id = self._get_session_id(user_id)

                # Get conversation history
                history = self._get_conversation_history(user_id)

                # Wait for extraction with timeout (Requirement 6.4)
                result = await asyncio.wait_for(
                    self.extraction_service.extract_and_store(
                        user_id=user_id,
                        session_id=session_id,
                        conversation_history=history,
                    ),
                    timeout=self.config.extraction_timeout_seconds,
                )
                extraction_success = result.success
                # Log result but continue regardless (Requirement 6.1)
                if not result.success:
                    logger.warning(
                        "Extraction failed for user %s: %s, proceeding with session clearing",
                        user_id,
                        result.error,
                    )

                # Generate + store summary (best-effort). This is explicitly
                # requested when /new is hit.
                if hasattr(self.extraction_service, "summarize_and_store"):
                    try:
                        summary_text = await asyncio.wait_for(
                            self.extraction_service.summarize_and_store(
                                user_id=user_id,
                                session_id=session_id,
                                conversation_history=history,
                            ),
                            timeout=self.config.extraction_timeout_seconds,
                        )
                    except asyncio.TimeoutError:
                        logger.warning(
                            "Summary generation timed out for user %s after %ds",
                            user_id,
                            self.config.extraction_timeout_seconds,
                        )
                    except Exception as e:
                        logger.warning(
                            "Summary generation failed for user %s: %s",
                            user_id,
                            e,
                        )
            except asyncio.TimeoutError:
                # Log warning and proceed (Requirement 6.4)
                logger.warning(
                    "Extraction timed out for user %s after %ds, proceeding with session clearing",
                    user_id,
                    self.config.extraction_timeout_seconds,
                )
            except Exception as e:
                # Log error and continue (Requirement 6.1)
                logger.error(
                    "Extraction failed for user %s: %s, proceeding with session clearing",
                    user_id,
                    e,
                )
        elif not self.memory_service and msg_count > 0:
            # Log that we're skipping due to unavailable memory service
            logger.warning(
                "Memory service unavailable for user %s, skipping extraction before new session",
                user_id,
            )

        # Always clear session and reset count (Requirement 6.2, 6.4)
        if user_id in self._conversation_history:
            del self._conversation_history[user_id]

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        self._user_sessions[user_id] = f"session_{user_id}_{timestamp}"
        self.reset_message_count(user_id)

        # Clear working folder on new session (Requirement 10.4)
        if self.file_service is not None:
            try:
                self.file_service.clear_working_folder(user_id)
                logger.info("Cleared working folder for user %s", user_id)
            except Exception as e:
                logger.warning("Failed to clear working folder for user %s: %s", user_id, e)

        # Build user notification message
        if extraction_success and msg_count > 0:
            notification = (
                "✨ Conversation analyzed and important information saved. New session started!"
            )
        else:
            notification = "✨ New session started!"

        if summary_text:
            notification = f"{notification}\n\n📝 Summary:\n{summary_text.strip()}"

        return self._create_agent(user_id), notification

    async def process_image_message(
        self,
        user_id: str,
        message: str,
        image_path: str,
    ) -> str:
        """Process a message with an image attachment.

        Attempts to use vision model if configured, falls back to default
        model, and handles errors gracefully by treating image as file.

        The Strands SDK uses the image_reader tool to process images from
        file paths. This method creates an agent with the image_reader tool
        and instructs it to analyze the image at the given path.

        Args:
            user_id: User's telegram ID.
            message: User's text message (caption).
            image_path: Path to the downloaded image file.

        Returns:
            Agent's response text.

        Requirements:
            - 3.1: Use vision model when configured
            - 3.3: Fall back to default model if not configured
            - 3.4: Fall back to file attachment if model doesn't support
            - 3.6: Include caption text with image
            - 8.4: Handle vision processing failures gracefully
        """
        # Keep shared skills mirrored into the user's directory even for
        # image messages (these may still lead to tool usage).
        self._sync_shared_skills_for_user(user_id)

        # Increment count for user message
        self.increment_message_count(user_id, 1)

        # Track user message for extraction
        prompt_text = message or f"[Image: {image_path}]"
        self._add_to_conversation_history(user_id, "user", prompt_text)

        # Retrieve memory context
        memory_context = None
        if self.config.memory_enabled and self.memory_service is not None:
            try:
                memory_context = self.memory_service.retrieve_memory_context(
                    user_id=user_id, query=message or "image analysis"
                )
            except Exception as e:
                logger.warning("Failed to retrieve memory: %s", e)

        try:
            # Try with vision model if configured (Req 3.1, 3.2)
            use_vision = bool(self.config.vision_model_id)
            model = self._create_model(use_vision=use_vision)

            # Use SlidingWindowConversationManager for session memory
            conversation_manager = SlidingWindowConversationManager(
                window_size=self.config.conversation_window_size,
            )

            # Import image_reader tool for vision processing
            try:
                from strands_tools import image_reader

                vision_tools = [image_reader]
            except ImportError:
                logger.warning("image_reader tool not available")
                vision_tools = []

            # Create agent with vision model and image_reader tool
            agent = Agent(
                model=model,
                conversation_manager=conversation_manager,
                tools=vision_tools,
                system_prompt=self._build_system_prompt(user_id, memory_context),
            )

            # Build prompt with image path and caption (Req 3.6)
            if message:
                prompt = f"Please analyze the image at: {image_path}\nUser's message: {message}"
            else:
                prompt = f"Please analyze the image at: {image_path}"

            result = agent(prompt)
            response = self._extract_response_text(result)

        except Exception as e:
            # Fall back to treating as file attachment (Req 3.4, 8.4)
            logger.warning(
                "Vision processing failed for user %s: %s, treating as file attachment", user_id, e
            )
            response = (
                "I received your image but couldn't process it visually. "
                f"The file is saved at: {image_path}\n\n"
                "You can ask me to read or analyze the file using "
                "file system tools."
            )

        # Track agent response for extraction
        self._add_to_conversation_history(user_id, "assistant", response)

        # Increment count for agent response
        self.increment_message_count(user_id, 1)

        return response

    async def process_message(self, user_id: str, message: str) -> str:
        """Process a user message through the agent.

        Retrieves relevant memory context based on the message and
        creates an agent with that context. Tracks message counts and
        triggers extraction when conversation limit is reached.

        Args:
            user_id: User's telegram ID.
            message: User's message to process.

        Returns:
            Agent's response text.
        """
        # Sync shared skills into user skills on every message.
        self._sync_shared_skills_for_user(user_id)

        # Increment count for user message (6.1)
        self.increment_message_count(user_id, 1)

        # Track user message for extraction
        self._add_to_conversation_history(user_id, "user", message)

        # If the user explicitly asks to remember something, store it
        # immediately (do not rely on end-of-session extraction).
        self._maybe_store_explicit_memory_request(user_id=user_id, message=message)

        # Retrieve memory context based on user's message
        memory_context = None
        if self.config.memory_enabled and self.memory_service is not None:
            try:
                memory_context = self.memory_service.retrieve_memory_context(
                    user_id=user_id, query=message
                )
                logger.info(
                    "Memory context for %s: facts=%d, prefs=%d",
                    user_id,
                    len(memory_context.get("facts", [])),
                    len(memory_context.get("preferences", [])),
                )
            except Exception as e:
                logger.warning("Failed to retrieve memory: %s", e)

        # Log model being used
        match self.config.model_provider:
            case ModelProvider.BEDROCK:
                model_id = self.config.bedrock_model_id
            case ModelProvider.GOOGLE:
                model_id = self.config.google_model_id
            case ModelProvider.OPENAI:
                model_id = self.config.openai_model_id
            case _:
                model_id = "unknown"
        logger.info(
            "Creating agent with model_provider=%s, model_id=%s",
            self.config.model_provider,
            model_id,
        )

        # Get previous messages to maintain conversation context
        previous_messages = self._get_user_messages(user_id)

        agent = self._create_agent(user_id, memory_context, messages=previous_messages)
        result = agent(message)
        response = self._extract_response_text(result)

        # Track agent response for extraction
        self._add_to_conversation_history(user_id, "assistant", response)

        # Increment count for agent response (6.1)
        self.increment_message_count(user_id, 1)

        # Check if extraction needed (6.1, 6.2)
        current_count = self.get_message_count(user_id)
        if current_count >= self.config.max_conversation_messages:
            # Trigger non-blocking extraction (6.2)
            if not self._extraction_in_progress.get(user_id, False):
                asyncio.create_task(self._trigger_extraction_and_clear(user_id))
                # Append notification to response (6.4)
                response = (
                    f"{response}\n\n"
                    "✨ Your conversation has been summarized and important "
                    "information saved. Starting fresh!"
                )

        return response

    async def process_message_with_attachments(
        self,
        user_id: str,
        message: str,
        attachments: list[dict],
    ) -> str:
        """Process a message with file attachments.

        Creates an agent with attachment context and processes the message.
        Tracks message counts and triggers extraction when limit is reached.

        Args:
            user_id: User's telegram ID.
            message: User's text message (may be empty).
            attachments: List of attachment metadata dicts with keys:
                - file_id: Telegram file ID
                - file_name: Sanitized filename
                - file_path: Full path to downloaded file
                - mime_type: MIME type if known
                - file_size: Size in bytes
                - is_image: Whether file is an image

        Returns:
            Agent's response text.

        Requirements:
            - 1.4: Forward file path and metadata to agent
            - 4.1: Agent has access to downloaded files
            - 4.2: Provide full path to downloaded files
            - 4.3: Include file metadata in context
        """
        # Sync shared skills into user skills on every message.
        self._sync_shared_skills_for_user(user_id)

        # Increment count for user message
        self.increment_message_count(user_id, 1)

        # Store explicit memory requests immediately even when attachments
        # are present.
        if message:
            self._add_to_conversation_history(user_id, "user", message)
            self._maybe_store_explicit_memory_request(
                user_id=user_id,
                message=message,
            )

        # Retrieve memory context
        memory_context = None
        if self.config.memory_enabled and self.memory_service is not None:
            try:
                query = message if message else "file attachment"
                memory_context = self.memory_service.retrieve_memory_context(
                    user_id=user_id, query=query
                )
                logger.info(
                    "Memory context for %s: facts=%d, prefs=%d",
                    user_id,
                    len(memory_context.get("facts", [])),
                    len(memory_context.get("preferences", [])),
                )
            except Exception as e:
                logger.warning("Failed to retrieve memory: %s", e)

        # Get previous messages to maintain conversation context
        previous_messages = self._get_user_messages(user_id)

        # Create agent with attachment context
        agent = self._create_agent(user_id, memory_context, attachments, messages=previous_messages)

        # Build prompt with file info if no message provided
        if message:
            prompt = message
        else:
            file_names = [att.get("file_name", "file") for att in attachments]
            prompt = f"I've sent you these files: {', '.join(file_names)}"

        # Ensure the prompt used is tracked for extraction.
        if not message:
            self._add_to_conversation_history(user_id, "user", prompt)

        result = agent(prompt)
        response = self._extract_response_text(result)

        # Track agent response for extraction
        self._add_to_conversation_history(user_id, "assistant", response)

        # Increment count for agent response
        self.increment_message_count(user_id, 1)

        # Check if extraction needed
        current_count = self.get_message_count(user_id)
        if current_count >= self.config.max_conversation_messages:
            if not self._extraction_in_progress.get(user_id, False):
                asyncio.create_task(self._trigger_extraction_and_clear(user_id))
                response = (
                    f"{response}\n\n"
                    "✨ Your conversation has been summarized and important "
                    "information saved. Starting fresh!"
                )

        return response

    def _maybe_store_explicit_memory_request(self, user_id: str, message: str) -> None:
        """Best-effort immediate memory write for explicit 'remember' requests.

        This is a deterministic fallback so explicit user requests persist even
        if the model fails to call the remember_* tools.
        """

        if not message:
            return
        if not self.config.memory_enabled or self.memory_service is None:
            return

        extracted = self._extract_explicit_memory_text(message)
        if not extracted:
            return

        kind, text = extracted
        if not text:
            return
        if self._contains_sensitive_memory_text(text):
            logger.info(
                "Skipping explicit memory store for user %s: looks sensitive",
                user_id,
            )
            return

        session_id = self._get_session_id(user_id)
        try:
            if kind == "preference" and hasattr(self.memory_service, "store_preference"):
                self.memory_service.store_preference(
                    user_id=user_id,
                    preference=text,
                    session_id=session_id,
                )
            else:
                self.memory_service.store_fact(
                    user_id=user_id,
                    fact=text,
                    session_id=session_id,
                    replace_similar=True,
                    similarity_query=text,
                )
        except Exception as e:
            logger.warning(
                "Failed to store explicit memory for user %s: %s",
                user_id,
                e,
            )

    def _extract_explicit_memory_text(
        self,
        message: str,
    ) -> tuple[str, str] | None:
        """Extract (kind, text) from messages like 'remember ...'.

        Returns:
            ("fact"|"preference", extracted_text) or None.
        """

        raw = message.strip()
        if not raw:
            return None

        lower = raw.lower().strip()

        prefixes = [
            "remember that ",
            "remember ",
            "please remember that ",
            "please remember ",
            "note that ",
            "note ",
            "save that ",
            "save this ",
        ]

        extracted = None
        for p in prefixes:
            if lower.startswith(p):
                extracted = raw[len(p) :].strip()
                break

        # Also support common punctuation patterns like "remember: ..."
        if extracted is None and lower.startswith("remember"):
            # Strip leading "remember" and any following punctuation.
            extracted = raw[len("remember") :].lstrip(" ,:-\t").strip()

        if not extracted:
            return None

        # Heuristic: if it reads like a preference, store as preference.
        pref_leads = (
            "i prefer ",
            "i like ",
            "i dislike ",
            "my preference ",
            "my preferences ",
            "prefer ",
        )
        kind = "preference" if extracted.lower().startswith(pref_leads) else "fact"
        return kind, extracted

    def _contains_sensitive_memory_text(self, text: str) -> bool:
        """Reject likely secrets/PII from being stored via explicit remember."""

        import re

        text_lower = text.lower()
        sensitive_keywords = [
            "password",
            "passwd",
            "pwd",
            "api_key",
            "apikey",
            "api-key",
            "secret",
            "token",
            "bearer",
            "private_key",
            "private-key",
            "access_key",
            "access-key",
            "credential",
            "auth_token",
        ]
        if any(k in text_lower for k in sensitive_keywords):
            return True

        patterns = [
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
            r"\b(?:password|passwd|pwd)\s*[:=]\s*\S+",
            r"\b(?:api[_-]?key|apikey)\s*[:=]\s*\S+",
            r"\b(?:token|bearer)\s*[:=]\s*\S+",
            r"\b(?:secret|private[_-]?key)\s*[:=]\s*\S+",
            r"\b(?:sk-|pk-)[A-Za-z0-9]{20,}",
            r"\bAKIA[A-Z0-9]{16}\b",
            r"\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b",
            r"\b[0-9]{16}\b",
        ]
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True

        return False

    async def _trigger_extraction_and_clear(self, user_id: str) -> None:
        """Trigger extraction and clear session (non-blocking).

        Invokes MemoryExtractionService when limit reached, clears session
        memory after extraction completes, and resets message count.

        Implements graceful degradation (Requirements 6.1, 6.2, 6.4):
        - Logs errors and continues with session clearing on failures
        - Skips extraction if memory service is unavailable
        - Uses asyncio.wait_for with configured timeout
        - Proceeds with session clearing after timeout

        Args:
            user_id: User's telegram ID.
        """
        # Mark extraction in progress to prevent duplicate triggers
        self._extraction_in_progress[user_id] = True

        try:
            # Check if extraction service and memory service are available
            # (Requirement 6.2: Skip extraction if memory service unavailable)
            if self.extraction_service and self.memory_service:
                session_id = self._get_session_id(user_id)

                # Get conversation history from session manager
                conversation_history = self._get_conversation_history(user_id)

                if conversation_history:
                    try:
                        # Use timeout to prevent blocking (Requirement 6.4)
                        result = await asyncio.wait_for(
                            self.extraction_service.extract_and_store(
                                user_id=user_id,
                                session_id=session_id,
                                conversation_history=conversation_history,
                            ),
                            timeout=self.config.extraction_timeout_seconds,
                        )
                        # Log result but continue regardless (Requirement 6.1)
                        if not result.success:
                            logger.warning(
                                "Extraction failed for user %s: %s",
                                user_id,
                                result.error,
                            )
                        else:
                            logger.info(
                                "Extraction complete for user %s: prefs=%d, facts=%d, commits=%d",
                                user_id,
                                len(result.preferences),
                                len(result.facts),
                                len(result.commitments),
                            )
                    except asyncio.TimeoutError:
                        # Log warning and proceed (Requirement 6.4)
                        logger.warning(
                            "Extraction timed out for user %s after %ds, "
                            "proceeding with session clearing",
                            user_id,
                            self.config.extraction_timeout_seconds,
                        )
                    except Exception as e:
                        # Log error and continue (Requirement 6.1)
                        logger.error(
                            "Extraction error for user %s: %s, proceeding with session clearing",
                            user_id,
                            e,
                        )
            elif not self.memory_service:
                # Log that we're skipping due to unavailable memory service
                logger.warning(
                    "Memory service unavailable for user %s, skipping extraction",
                    user_id,
                )
        finally:
            # Always clear session and reset count (Requirement 6.2, 6.4)
            self._clear_session_memory(user_id)
            self.reset_message_count(user_id)
            self._extraction_in_progress[user_id] = False

    def _get_conversation_history(self, user_id: str) -> list[dict]:
        """Get conversation history for a user.

        Args:
            user_id: User's telegram ID.

        Returns:
            List of message dicts with role and content.
        """
        return self._conversation_history.get(user_id, [])

    def _add_to_conversation_history(self, user_id: str, role: str, content: str) -> None:
        """Add a message to the conversation history.

        Args:
            user_id: User's telegram ID.
            role: Message role ('user' or 'assistant').
            content: Message content.
        """
        if user_id not in self._conversation_history:
            self._conversation_history[user_id] = []
        self._conversation_history[user_id].append({"role": role, "content": content})

    def _clear_session_memory(self, user_id: str) -> None:
        """Clear session memory for a user.

        Args:
            user_id: User's telegram ID.
        """
        # Clear conversation history
        if user_id in self._conversation_history:
            del self._conversation_history[user_id]

        # Clear conversation manager to reset agent's memory
        if user_id in self._user_conversation_managers:
            del self._user_conversation_managers[user_id]

        # Clear cached agent instance
        if user_id in self._user_agents:
            del self._user_agents[user_id]

        # Create new session ID
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        self._user_sessions[user_id] = f"session_{user_id}_{timestamp}"

        logger.info("Cleared session memory for user %s", user_id)

    def _get_media_type_from_extension(self, file_path: str | Path) -> str:
        """Determine media type from file extension.

        Args:
            file_path: Path to the image file.

        Returns:
            MIME type string for the image.

        Requirements:
            - 3.6: Determine media type from extension for vision model
        """
        ext = Path(file_path).suffix.lower()
        media_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        return media_types.get(ext, "image/png")

    def _prepare_image_content(
        self,
        image_path: str | Path,
        caption: str | None = None,
    ) -> list[dict]:
        """Prepare image content for vision model input.

        Base64 encodes the image and formats it for the model's expected
        input structure. Includes caption text if provided.

        Args:
            image_path: Path to the image file.
            caption: Optional text caption to include with the image.

        Returns:
            List of content blocks for the model (image + optional text).

        Requirements:
            - 3.6: Include caption text with image in context
        """
        import base64

        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode()

        media_type = self._get_media_type_from_extension(image_path)

        content = []

        # Add image content block
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": image_data,
                },
            }
        )

        # Add caption text if provided (Req 3.6)
        if caption:
            content.append(
                {
                    "type": "text",
                    "text": caption,
                }
            )

        return content

    def _extract_response_text(self, result) -> str:
        """Extract text response from agent result.

        Extracts all text blocks from the agent result and concatenates them,
        filtering out thinking blocks (wrapped in <thinking> tags).

        Args:
            result: Agent result object with message content.

        Returns:
            Concatenated text response without thinking blocks.
        """
        import re

        if hasattr(result, "message") and result.message:
            content = result.message.get("content", [])
            text_parts = []
            for block in content:
                if "text" in block:
                    text = block["text"]
                    # Remove thinking blocks (content between <thinking> tags)
                    text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL)
                    # Clean up any leftover whitespace from removed blocks
                    text = re.sub(r"\n{3,}", "\n\n", text).strip()
                    if text:
                        text_parts.append(text)

            if text_parts:
                # Return the last non-empty text part (final response)
                # This avoids duplicates when agent outputs multiple versions
                return text_parts[-1]

        return str(result)

    def get_model_provider(self) -> ModelProvider:
        """Get the currently configured model provider."""
        return self.config.model_provider

    def cleanup_user(self, user_id: str) -> None:
        """Clean up resources for a user."""
        self._conversation_history.pop(user_id, None)
        self._user_agent_names.pop(user_id, None)
        self._user_message_counts.pop(user_id, None)
        self._extraction_in_progress.pop(user_id, None)
        self._user_conversation_managers.pop(user_id, None)
        self._user_agents.pop(user_id, None)

    def reload_agent(self, user_id: str) -> Agent:
        """Reload agent to pick up new skills."""
        return self._create_agent(user_id)

    def get_user_skills_directory(self, user_id: str) -> str:
        """Get the skills directory path for a user."""
        return str(self._get_user_skills_dir(user_id))

    def get_session_id(self, user_id: str) -> str | None:
        """Get the current session ID for a user."""
        return self._user_sessions.get(user_id)
