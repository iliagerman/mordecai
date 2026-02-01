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
import logging
import os
import random
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager

from app.config import AgentConfig
from app.enums import LogSeverity, ModelProvider
from app.observability.trace_context import new_trace_id, set_trace
from app.observability.trace_logging import trace_event
from app.services.agent.explicit_memory import (
    ExplicitMemoryWriter,
    contains_sensitive_memory_text,
    extract_explicit_memory_text,
)
from app.services.agent.model_factory import ModelFactory
from app.services.agent.prompt_builder import SystemPromptBuilder
from app.services.agent.response_extractor import extract_response_text
from app.services.agent.skills import SkillRepository
from app.services.agent.test_skill_runner import DeterministicEchoSkillRunner
from app.services.agent.types import AttachmentInfo, ConversationMessage, MemoryContext
from app.services.personality_service import PersonalityService
from app.tools import (
    cron_tools as cron_tools_module,
)
from app.tools import (
    download_skill as download_skill_module,
)
from app.tools import (
    file_read_env as file_read_env_module,
)
from app.tools import (
    file_write_env as file_write_env_module,
)
from app.tools import (
    onboard_pending_skills as onboard_pending_skills_module,
)
from app.tools import (
    personality_vault as personality_vault_module,
)
from app.tools import (
    remember_memory as remember_memory_module,
)
from app.tools import (
    search_memory as search_memory_module,
)
from app.tools import (
    send_file as send_file_module,
)
from app.tools import (
    set_agent_name as set_agent_name_tool,
)
from app.tools import (
    shell_env as shell_env_module,
)
from app.tools import (
    skill_secrets as skill_secrets_module,
)

if TYPE_CHECKING:
    from strands.models.model import Model

    from app.services.cron_service import CronService
    from app.services.file_service import FileService
    from app.services.memory_extraction_service import MemoryExtractionService
    from app.services.memory_service import MemoryService
    from app.services.pending_skill_service import PendingSkillService

logger = logging.getLogger(__name__)


### NOTE
# A large amount of functionality previously lived directly in this module.
# It has been extracted into smaller, typed helper components under
# `app.services.agent.*` to keep this service focused on orchestration.


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
        skill_service: Any | None = None,
        extraction_service: "MemoryExtractionService | None" = None,
        file_service: "FileService | None" = None,
        pending_skill_service: "PendingSkillService | None" = None,
        logging_service: Any | None = None,
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
        self.logging_service = logging_service
        self._user_sessions: dict[str, str] = {}
        self._user_agent_names: dict[str, str | None] = {}  # cached names
        self._conversation_history: dict[str, list[ConversationMessage]] = {}  # extraction
        self._user_message_counts: dict[str, int] = {}  # per-user counts
        self._extraction_in_progress: dict[str, bool] = {}  # track extractions
        self._user_agents: dict[str, Agent] = {}  # cached agent instances
        self._user_conversation_managers: dict[
            str, SlidingWindowConversationManager
        ] = {}  # cached managers

        # Cached copy of the user's Obsidian STM scratchpad content.
        # This enables "handoff" behavior when we clear the STM file after
        # writing a session summary (e.g., on /new or when max messages is hit),
        # while still injecting the most recent STM into the next system prompt.
        self._obsidian_stm_cache: dict[str, str] = {}

        # External personality/identity loader (Obsidian vault)
        self.personality_service = PersonalityService(
            config.obsidian_vault_root,
            max_chars=getattr(config, "personality_max_chars", 20_000),
        )

        # Extracted helper components
        self._skill_repo = SkillRepository(config)
        self._model_factory = ModelFactory(config)
        self._prompt_builder = SystemPromptBuilder(
            config=config,
            skill_repo=self._skill_repo,
            personality_service=self.personality_service,
            working_dir_resolver=self._get_user_working_dir,
            obsidian_stm_cache=self._obsidian_stm_cache,
            user_agent_names=self._user_agent_names,
            has_cron=self.cron_service is not None,
        )
        self._explicit_memory_writer = ExplicitMemoryWriter(
            config=config,
            memory_service=self.memory_service,
            get_session_id=self._get_session_id,
            logger=logger,
        )
        self._deterministic_skill_runner = DeterministicEchoSkillRunner(
            config=config,
            skill_repo=self._skill_repo,
            get_working_dir=self._get_user_working_dir,
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

    def _load_merged_skill_secrets(self, user_id: str) -> dict[str, Any]:
        return self._skill_repo.load_merged_skill_secrets(user_id)

    def _get_missing_skill_requirements(self, user_id: str) -> dict[str, dict[str, list[dict]]]:
        # Maintain historical return shape for unit tests that treat these as plain dicts.
        missing = self._skill_repo.get_missing_skill_requirements(user_id)
        return cast(dict[str, dict[str, list[dict]]], missing)

    def _get_user_skills_dir(self, user_id: str) -> Path:
        """Get the skills directory for a specific user.

        Also syncs shared skills into the user's directory so Strands can
        load all tools from a single directory.

        Current behavior: shared skills are mirrored into the user directory
        on every call, overwriting any existing same-named entries. This keeps
        the per-user tools directory always in sync with shared skills.
        """
        return self._skill_repo.get_user_skills_dir(user_id, create=True)

    def _sync_shared_skills_for_user(self, user_id: str) -> Path:
        """Sync shared skills into the user's skills directory.

        This is an explicit helper so we can guarantee sync happens at the
        start of every message-processing path (even if agent caching/reuse
        changes in the future).

        Returns:
            The user's skills directory path.
        """
        return self._skill_repo.sync_shared_skills_for_user(user_id)

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
        self._skill_repo.sync_shared_skills(user_dir)

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
        return self._model_factory.create(use_vision=use_vision)

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

    def _build_system_prompt(
        self,
        user_id: str,
        memory_context: MemoryContext | None = None,
        attachments: list[AttachmentInfo] | None = None,
    ) -> str:
        """Build the system prompt for the agent.

        Kept as a wrapper because unit tests call this private method directly.
        """
        return self._prompt_builder.build(
            user_id=user_id,
            memory_context=memory_context,
            attachments=attachments,
        )

    def _discover_skills(self, user_id: str) -> list[dict]:
        """Discover installed instruction-based skills for a user.

        Skills are instruction-based: they contain a SKILL.md file with
        step-by-step instructions that the agent must read and follow.

        Shared skills are mirrored into the per-user skills directory on each
        message. Therefore, the single source of truth for what the agent can
        load is the user's directory.
        """
        return cast(list[dict], self._skill_repo.discover(user_id))

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

    def _get_user_messages(self, user_id: str) -> list[Any]:
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
        memory_context: MemoryContext | None = None,
        attachments: list[AttachmentInfo] | None = None,
        messages: list[Any] | None = None,
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
        if self.config.memory_enabled and self.memory_service is not None:
            session_id = self._get_session_id(user_id)

            # Best-effort: create an AgentCore session manager so downstream
            # tools and integrations have an initialized memory session.
            # If AgentCore is unavailable/misconfigured, degrade gracefully.
            try:
                self.memory_service.create_session_manager(
                    user_id=user_id,
                    session_id=session_id,
                )
            except Exception as e:
                logger.warning(
                    "Memory session manager unavailable for user %s (degrading gracefully): %s",
                    user_id,
                    e,
                )

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

        # Shell wrapper context: refresh env from secrets.yml before every shell command
        shell_env_module.set_shell_env_context(
            user_id=user_id,
            secrets_path=getattr(self.config, "secrets_path", "secrets.yml"),
            config=self.config,
        )

        # Skill secrets tool context: persist env vars into secrets.yml
        skill_secrets_module.set_skill_secrets_context(
            user_id=user_id,
            secrets_path=getattr(self.config, "secrets_path", "secrets.yml"),
            config=self.config,
        )

        # Built-in Strands tools (not the same thing as instruction-based “skills”).
        # Skills are loaded separately from the skills directory via load_tools_from_directory.
        builtin_tools = [
            shell_env_module.shell,
            file_read_env_module.file_read,
            file_write_env_module.file_write,
            set_agent_name_tool,
            send_file_module,
        ]

        # Built-in tools for persisting per-skill settings into secrets.yml
        # (used during skill onboarding / setup prompts).
        builtin_tools.append(skill_secrets_module.set_skill_env_vars)
        builtin_tools.append(skill_secrets_module.set_skill_config)

        # Add personality vault tools (read/write soul.md + id.md under me/<TELEGRAM_ID>/)
        builtin_tools.extend(
            [
                personality_vault_module.personality_read,
                personality_vault_module.personality_write,
                personality_vault_module.personality_reset_to_default,
            ]
        )

        # Add search_memory tool if memory service is available
        if self.config.memory_enabled and self.memory_service is not None:
            builtin_tools.append(search_memory_module.search_memory)
            builtin_tools.extend(
                [
                    remember_memory_module.remember_fact,
                    remember_memory_module.remember_preference,
                    remember_memory_module.remember,
                ]
            )

        # Add cron tools if cron service is available
        if self.cron_service is not None:
            builtin_tools.extend(
                [
                    cron_tools_module.create_cron_task,
                    cron_tools_module.list_cron_tasks,
                    cron_tools_module.delete_cron_task,
                ]
            )

        # Add pending skill onboarding tools if service is available
        if self.pending_skill_service is not None:
            builtin_tools.extend(
                [
                    onboard_pending_skills_module.list_pending_skills,
                    onboard_pending_skills_module.onboard_pending_skills,
                    onboard_pending_skills_module.repair_skill_dependencies,
                ]
            )

        # Add skill download tool if skill service is available
        if self.skill_service is not None:
            builtin_tools.append(download_skill_module.download_skill_to_pending)

        agent = Agent(
            model=model,
            messages=messages,
            conversation_manager=conversation_manager,
            tools=builtin_tools,
            # Strands' type hints declare this parameter as bool, but we rely on
            # runtime backward-compat behavior where a string path is accepted.
            load_tools_from_directory=cast(Any, user_skills_dir),
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
        if user_id in self._user_agents:
            return self._user_agents[user_id]
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

        # Capture the current session_id (for tagging any summary/extraction) before
        # we clear in-memory session state.
        session_id = self._get_session_id(user_id)

        # On /new, we want to persist a summary to Obsidian STM before wiping context,
        # even if long-term memory is unavailable.
        #
        # Extraction into long-term memory is best-effort and only runs when the
        # memory service is available.
        if self.extraction_service and msg_count > 0:
            try:
                # Get conversation history
                history = self._get_conversation_history(user_id)
                history_for_extraction = cast(list[dict[str, str]], history)

                if self.memory_service is not None:
                    # Wait for extraction with timeout (Requirement 6.4)
                    result = await asyncio.wait_for(
                        self.extraction_service.extract_and_store(
                            user_id=user_id,
                            session_id=session_id,
                            conversation_history=history_for_extraction,
                        ),
                        timeout=self.config.extraction_timeout_seconds,
                    )
                    extraction_success = bool(result and result.success)
                    # Log result but continue regardless (Requirement 6.1)
                    if not extraction_success:
                        logger.warning(
                            "Extraction failed for user %s: %s, proceeding with session clearing",
                            user_id,
                            getattr(result, "error", None),
                        )
                else:
                    logger.warning(
                        "Memory service unavailable for user %s, skipping extraction before new session",
                        user_id,
                    )

                # Generate + store summary (best-effort). This is explicitly
                # requested when /new is hit.
                if hasattr(self.extraction_service, "summarize_and_store"):
                    try:
                        summary_text = await asyncio.wait_for(
                            self.extraction_service.summarize_and_store(
                                user_id=user_id,
                                session_id=session_id,
                                conversation_history=history_for_extraction,
                            ),
                            timeout=self.config.extraction_timeout_seconds,
                        )
                    except TimeoutError:
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
            except TimeoutError:
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

        # If we wrote a session summary into Obsidian STM, snapshot it into an
        # in-memory cache for immediate injection into the next session prompt,
        # then clear stm.md on disk. This implements an explicit “handoff” model:
        # the prior session's STM is preserved for the next prompt, but the
        # scratchpad is reset for the new session.
        vault_root = getattr(self.config, "obsidian_vault_root", None)
        if vault_root and summary_text:
            try:
                from app.tools.short_term_memory_vault import (
                    append_session_summary,
                    read_raw_text,
                    short_term_memory_path,
                )
                from app.tools.short_term_memory_vault import (
                    clear as clear_stm,
                )

                # Make sure the session summary is appended to STM even if the
                # extraction service couldn't write it (e.g., due to config
                # differences) or long-term memory is unavailable.
                try:
                    stm_path = short_term_memory_path(vault_root, user_id)
                    already_has_block = False
                    if stm_path.exists() and stm_path.is_file():
                        try:
                            existing = stm_path.read_text(encoding="utf-8")
                            already_has_block = f"## Session summary: {session_id}" in existing
                        except Exception:
                            already_has_block = False

                    if not already_has_block:
                        append_session_summary(
                            vault_root,
                            user_id,
                            session_id,
                            summary_text,
                            max_chars=getattr(self.config, "personality_max_chars", 20_000),
                        )
                except Exception:
                    # Never fail /new due to Obsidian write issues.
                    pass

                # Snapshot STM into an in-memory handoff cache for the next prompt.
                # IMPORTANT: Even if STM read fails, we still want to clear the file
                # on disk; and we can fall back to a minimal synthesized block.
                stm_text: str | None = None
                try:
                    stm_text = read_raw_text(
                        vault_root,
                        user_id,
                        max_chars=getattr(self.config, "personality_max_chars", 20_000),
                    )
                except Exception:
                    stm_text = None

                if not stm_text:
                    # Fallback: construct the minimal expected handoff block so
                    # the next session prompt still includes the prior summary.
                    # (We don't include created_at here; it's not required for prompt injection.)
                    stm_text = (
                        "# STM\n\n"
                        f"## Session summary: {session_id}\n\n"
                        f"{(summary_text or '').strip()}\n"
                    )

                if stm_text:
                    self._obsidian_stm_cache[user_id] = stm_text
            except Exception:
                # Never fail /new due to Obsidian handling issues.
                pass
            finally:
                # Clear the on-disk scratchpad after we snapshot it.
                try:
                    from app.tools.short_term_memory_vault import clear as clear_stm

                    clear_stm(vault_root, user_id)
                except Exception:
                    pass

        # Always clear session and reset count (Requirement 6.2, 6.4)
        self._clear_session_memory(user_id)
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
        memory_context: MemoryContext | None = None
        if self.config.memory_enabled and self.memory_service is not None:
            try:
                memory_context = cast(
                    MemoryContext,
                    self.memory_service.retrieve_memory_context(
                        user_id=user_id, query=message or "image analysis"
                    ),
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
        trace_this = False
        trace_id: str | None = None
        if getattr(self.config, "trace_enabled", False):
            try:
                sample_rate = float(getattr(self.config, "trace_sample_rate", 1.0) or 0.0)
                sample_rate = min(max(sample_rate, 0.0), 1.0)
            except Exception:
                sample_rate = 1.0
            trace_this = random.random() <= sample_rate

        if trace_this:
            trace_id = new_trace_id()
            set_trace(trace_id=trace_id, actor_id=user_id)

        t0 = time.perf_counter()

        # ------------------------------------------------------------------
        # Pytest-only deterministic fallback for simple skills
        # ------------------------------------------------------------------
        # Integration tests can be flaky because the model may choose to
        # "investigate" instead of executing a trivial SKILL.md command.
        # When running under pytest, if the user requests a specific skill and
        # that skill's SKILL.md contains a single safe echo command, execute it
        # deterministically via the shell tool.
        if os.getenv("PYTEST_CURRENT_TEST"):
            deterministic = self._maybe_run_simple_skill_echo_for_tests(
                user_id=user_id,
                message=message,
            )
            if deterministic is not None:
                # Keep behavior closer to the normal flow (sync + counts).
                self._sync_shared_skills_for_user(user_id)
                self.increment_message_count(user_id, 1)
                # Track both sides for extraction consistency
                self._add_to_conversation_history(user_id, "user", message)
                self._add_to_conversation_history(user_id, "assistant", deterministic)
                self.increment_message_count(user_id, 1)
                return deterministic

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
        memory_context: MemoryContext | None = None
        if self.config.memory_enabled and self.memory_service is not None:
            try:
                memory_context = cast(
                    MemoryContext,
                    self.memory_service.retrieve_memory_context(user_id=user_id, query=message),
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

        if trace_this:
            fields: dict = {
                "model_provider": str(self.config.model_provider),
                "model_id": model_id,
                "session_id": self._get_session_id(user_id),
                "memory_enabled": bool(self.config.memory_enabled),
            }
            if getattr(self.config, "trace_model_io_enabled", False):
                fields.update(
                    {
                        "user_message": message,
                        "user_message_len": len(message or ""),
                    }
                )
            trace_event(
                "agent.message.start",
                max_chars=getattr(self.config, "trace_max_chars", 2000),
                **fields,
            )

            # Best-effort: persist a compact milestone into DB-backed activity logs
            # so users can view recent agent activity via Telegram /logs.
            if self.logging_service is not None:
                try:
                    details = {
                        "trace_id": trace_id,
                        "model_provider": str(self.config.model_provider),
                        "model_id": model_id,
                    }
                    await self.logging_service.log_action(
                        user_id=user_id,
                        action="Processed message: started",
                        severity=LogSeverity.INFO,
                        details=details,
                    )
                except Exception:
                    # Never break message processing due to logging.
                    pass
        logger.info(
            "Creating agent with model_provider=%s, model_id=%s",
            self.config.model_provider,
            model_id,
        )

        # Get previous messages to maintain conversation context
        previous_messages = self._get_user_messages(user_id)

        try:
            agent = self._create_agent(user_id, memory_context, messages=previous_messages)
            result = agent(message)
            response = self._extract_response_text(result)
        except Exception as e:
            if trace_this:
                trace_event(
                    "agent.message.error",
                    max_chars=getattr(self.config, "trace_max_chars", 2000),
                    error=str(e),
                    error_type=type(e).__name__,
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                )

                if self.logging_service is not None:
                    try:
                        await self.logging_service.log_action(
                            user_id=user_id,
                            action="Processed message: failed",
                            severity=LogSeverity.ERROR,
                            details={
                                "trace_id": trace_id,
                                "error": str(e),
                                "error_type": type(e).__name__,
                            },
                        )
                    except Exception:
                        pass
            raise

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

        if trace_this:
            fields = {
                "duration_ms": int((time.perf_counter() - t0) * 1000),
                "message_count": self.get_message_count(user_id),
            }
            if getattr(self.config, "trace_model_io_enabled", False):
                fields.update(
                    {
                        "assistant_response": response,
                        "assistant_response_len": len(response or ""),
                    }
                )
            trace_event(
                "agent.message.end",
                max_chars=getattr(self.config, "trace_max_chars", 2000),
                **fields,
            )

            if self.logging_service is not None:
                try:
                    await self.logging_service.log_action(
                        user_id=user_id,
                        action="Processed message: completed",
                        severity=LogSeverity.INFO,
                        details={
                            "trace_id": trace_id,
                            "duration_ms": fields.get("duration_ms"),
                        },
                    )
                except Exception:
                    pass

        return response

    def _maybe_run_simple_skill_echo_for_tests(self, *, user_id: str, message: str) -> str | None:
        """Pytest-only: run a simple echo-only skill deterministically.

        Returns a human-readable response if it executed a skill, otherwise None.
        """

        return self._deterministic_skill_runner.maybe_run(user_id=user_id, message=message)

    async def process_message_with_attachments(
        self,
        user_id: str,
        message: str,
        attachments: list[AttachmentInfo],
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
        memory_context: MemoryContext | None = None
        if self.config.memory_enabled and self.memory_service is not None:
            try:
                query = message if message else "file attachment"
                memory_context = cast(
                    MemoryContext,
                    self.memory_service.retrieve_memory_context(user_id=user_id, query=query),
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
        self._explicit_memory_writer.maybe_store(user_id=user_id, message=message)

    def _extract_explicit_memory_text(
        self,
        message: str,
    ) -> tuple[str, str] | None:
        return extract_explicit_memory_text(message)

    def _contains_sensitive_memory_text(self, text: str) -> bool:
        """Reject likely secrets/PII from being stored via explicit remember."""
        return contains_sensitive_memory_text(text)

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
            session_id = self._get_session_id(user_id)
            conversation_history = self._get_conversation_history(user_id)
            history_for_extraction = cast(list[dict[str, str]], conversation_history)

            # Always generate + store a session summary to Obsidian STM before clearing.
            # This is independent from AgentCore memory availability.
            summary_text: str | None = None
            if self.extraction_service and conversation_history:
                if hasattr(self.extraction_service, "summarize_and_store"):
                    try:
                        summary_text = await asyncio.wait_for(
                            self.extraction_service.summarize_and_store(
                                user_id=user_id,
                                session_id=session_id,
                                conversation_history=history_for_extraction,
                            ),
                            timeout=self.config.extraction_timeout_seconds,
                        )
                    except TimeoutError:
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

            # Extract into long-term memory when available.
            if self.extraction_service and self.memory_service and conversation_history:
                try:
                    result = await asyncio.wait_for(
                        self.extraction_service.extract_and_store(
                            user_id=user_id,
                            session_id=session_id,
                            conversation_history=history_for_extraction,
                        ),
                        timeout=self.config.extraction_timeout_seconds,
                    )
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
                except TimeoutError:
                    logger.warning(
                        "Extraction timed out for user %s after %ds, proceeding with session clearing",
                        user_id,
                        self.config.extraction_timeout_seconds,
                    )
                except Exception as e:
                    logger.error(
                        "Extraction error for user %s: %s, proceeding with session clearing",
                        user_id,
                        e,
                    )
            elif not self.memory_service:
                logger.warning(
                    "Memory service unavailable for user %s, skipping extraction",
                    user_id,
                )

            # Snapshot Obsidian STM into cache for the next session and clear it.
            vault_root = getattr(self.config, "obsidian_vault_root", None)
            if vault_root and summary_text:
                try:
                    from app.tools.short_term_memory_vault import read_raw_text

                    stm_text = read_raw_text(
                        vault_root,
                        user_id,
                        max_chars=getattr(self.config, "personality_max_chars", 20_000),
                    )
                    if stm_text:
                        self._obsidian_stm_cache[user_id] = stm_text
                except Exception:
                    pass

                try:
                    from app.tools.short_term_memory_vault import clear as clear_stm

                    clear_stm(vault_root, user_id)
                except Exception:
                    pass
        finally:
            # Always clear session and reset count (Requirement 6.2, 6.4)
            self._clear_session_memory(user_id)
            self.reset_message_count(user_id)
            self._extraction_in_progress[user_id] = False

    async def consolidate_short_term_memories_daily(self) -> None:
        """Internal daily job: promote Obsidian short-term memories into LTM.

        Source of truth for short-term memory:
                    <vault>/me/<USER_ID>/stm.md

        This method is intended to be called by a *system* cron task that is
        registered in code (not DB-backed), so it is not user-editable.

        Behavior:
        - For each user folder under <vault>/me/* (excluding 'default'):
                    - If stm.md exists and is non-empty:
            - Extract important facts/preferences into long-term memory.
            - Optionally store a concise summary.
                        - Delete stm.md to start fresh.
        - If extraction fails for a user, we DO NOT delete the file.
        """

        vault_root = getattr(self.config, "obsidian_vault_root", None)
        if not vault_root:
            logger.debug("Short-term memory consolidation skipped: vault not configured")
            return

        if self.memory_service is None:
            logger.debug("Short-term memory consolidation skipped: memory service unavailable")
            return

        try:
            from app.tools.short_term_memory_vault import clear, list_user_ids, read_raw_text
        except Exception as e:
            logger.debug("Short-term memory consolidation skipped: %s", e)
            return

        day_stamp = datetime.utcnow().strftime("%Y%m%d")
        session_id = f"stm_daily_{day_stamp}"

        user_ids = list_user_ids(vault_root)
        if not user_ids:
            return

        logger.info(
            "Running daily short-term memory consolidation for %d user(s)",
            len(user_ids),
        )

        for user_id in user_ids:
            try:
                raw = read_raw_text(
                    vault_root,
                    user_id,
                    max_chars=getattr(self.config, "personality_max_chars", 20_000),
                )
                if not raw:
                    continue

                # Provide a deterministic, two-message conversation so the
                # extraction service doesn't skip (<2 messages).
                conversation_history = [
                    {
                        "role": "user",
                        "content": (
                            "Please extract and preserve the important facts and preferences from "
                            "the following short-term memory scratchpad. "
                            "Short-term memories may correct older long-term memories; "
                            "prefer the newest statements.\n\n" + raw
                        ),
                    },
                    {"role": "assistant", "content": "Understood."},
                ]

                promoted_ok = False

                if self.extraction_service is not None:
                    result = await self.extraction_service.extract_and_store(
                        user_id=user_id,
                        session_id=session_id,
                        conversation_history=conversation_history,
                    )
                    promoted_ok = bool(result and result.success)

                    # Best-effort: store a summary too (if available).
                    if hasattr(self.extraction_service, "summarize_and_store"):
                        try:
                            await self.extraction_service.summarize_and_store(
                                user_id=user_id,
                                session_id=session_id,
                                conversation_history=conversation_history,
                            )
                        except Exception as e:
                            logger.debug(
                                "Short-term summary storage failed for user %s: %s",
                                user_id,
                                e,
                            )
                else:
                    # Degraded mode: store a snapshot as a fact.
                    # NOTE: We do not write this back to short-term.
                    promoted_ok = self.memory_service.store_fact(
                        user_id=user_id,
                        fact=f"Short-term memories snapshot ({session_id}):\n{raw}",
                        session_id=session_id,
                        replace_similar=False,
                    )

                if promoted_ok:
                    if not clear(vault_root, user_id):
                        logger.warning(
                            "Failed to clear short-term memories for user %s",
                            user_id,
                        )
                else:
                    logger.warning(
                        "Short-term consolidation failed for user %s; keeping file for retry",
                        user_id,
                    )
            except Exception as e:
                logger.warning(
                    "Short-term consolidation error for user %s: %s",
                    user_id,
                    e,
                )

    def _get_conversation_history(self, user_id: str) -> list[ConversationMessage]:
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
        # Keep the key present but reset to an empty list. Some unit tests
        # assert the cleared state is `[]` (not missing/None).
        self._conversation_history[user_id] = []

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

    def _extract_response_text(self, result: Any) -> str:
        """Extract text response from agent result.

        Extracts all text blocks from the agent result and concatenates them,
        filtering out thinking blocks (wrapped in <thinking> tags).

        Args:
            result: Agent result object with message content.

        Returns:
            Concatenated text response without thinking blocks.
        """
        return extract_response_text(result)

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
