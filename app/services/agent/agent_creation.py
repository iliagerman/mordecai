"""Agent creation functionality for AgentService.

This module handles agent instance creation including:
- Creating Strands Agent instances with proper configuration
- Managing agent caching
- Setting up tools and context
- Managing conversation managers
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.models.model import Model

from app.models.agent import AttachmentInfo, MemoryContext

if TYPE_CHECKING:
    from app.config import AgentConfig
    from app.services.agent.model_factory import ModelFactory
    from app.services.agent.prompt_builder import SystemPromptBuilder
    from app.services.agent.skills import SkillRepository
    from app.services.agent.state import SessionManager as SessionIdManager
    from app.services.cron_service import CronService
    from app.services.file_service import FileService
    from app.services.memory_service import MemoryService
    from app.services.pending_skill_service import PendingSkillService

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

logger = logging.getLogger(__name__)


class AgentCreator:
    """Handles agent instance creation and management."""

    def __init__(
        self,
        config: AgentConfig,
        memory_service: MemoryService | None,
        cron_service: CronService | None,
        file_service: FileService | None,
        pending_skill_service: PendingSkillService | None,
        skill_service: Any | None,
        session_manager: SessionIdManager,
        skill_repo: SkillRepository,
        model_factory: ModelFactory,
        prompt_builder: SystemPromptBuilder,
        user_conversation_managers: dict,
        user_agents: dict,
        get_session_id: Callable[[str], str],
        on_agent_name_changed: Callable[[str, str], None],
    ):
        """Initialize the agent creator.

        Args:
            config: Application configuration.
            memory_service: Optional MemoryService.
            cron_service: Optional CronService.
            file_service: Optional FileService.
            pending_skill_service: Optional PendingSkillService.
            skill_service: Optional skill service.
            session_manager: Session ID manager.
            skill_repo: Skills repository.
            model_factory: Model factory.
            prompt_builder: System prompt builder.
            user_conversation_managers: Dict of user conversation managers.
            user_agents: Dict of user agents (will be mutated).
            get_session_id: Function to get session ID.
            on_agent_name_changed: Callback for agent name changes.
        """
        self.config = config
        self.memory_service = memory_service
        self.cron_service = cron_service
        self.file_service = file_service
        self.pending_skill_service = pending_skill_service
        self.skill_service = skill_service
        self._session_manager = session_manager
        self._skill_repo = skill_repo
        self._model_factory = model_factory
        self._prompt_builder = prompt_builder
        self._user_conversation_managers = user_conversation_managers
        self._user_agents = user_agents
        self._get_session_id = get_session_id
        self._on_agent_name_changed = on_agent_name_changed

    def create_model(self, use_vision: bool = False) -> Model:
        """Create a model instance."""
        return self._model_factory.create(use_vision=use_vision)

    def get_or_create_conversation_manager(self, user_id: str) -> SlidingWindowConversationManager:
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

    def get_user_messages(self, user_id: str) -> list[Any]:
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

    def cache_agent(self, user_id: str, agent: Agent) -> None:
        """Cache an agent instance for a user.

        Args:
            user_id: User's telegram ID.
            agent: Agent instance to cache.
        """
        self._user_agents[user_id] = agent

    def get_or_create_agent(self, user_id: str) -> Agent:
        """Create an agent for user."""
        if user_id in self._user_agents:
            return self._user_agents[user_id]
        return self.create_agent(user_id)

    def discover_skills(self, user_id: str) -> list[dict]:
        """Discover installed instruction-based skills for a user.

        Skills are instruction-based: they contain a SKILL.md file with
        step-by-step instructions that the agent must read and follow.

        Shared skills are mirrored into the per-user skills directory on each
        message. Therefore, the single source of truth for what the agent can
        load is the user's directory.
        """
        return cast(list[dict], self._skill_repo.discover(user_id))

    def get_user_skills_dir(self, user_id: str) -> Path:
        """Get the skills directory for a specific user.

        Also syncs shared skills into the user's directory so Strands can
        load all tools from a single directory.

        Current behavior: shared skills are mirrored into the user directory
        on every call, overwriting any existing same-named entries. This keeps
        the per-user tools directory always in sync with shared skills.
        """
        return self._skill_repo.get_user_skills_dir(user_id, create=True)

    def sync_shared_skills_for_user(self, user_id: str) -> Path:
        """Sync shared skills into the user's skills directory.

        This is an explicit helper so we can guarantee sync happens at the
        start of every message-processing path (even if agent caching/reuse
        changes in the future).

        Returns:
            The user's skills directory path.
        """
        return self._skill_repo.sync_shared_skills_for_user(user_id)

    def load_merged_skill_secrets(self, user_id: str) -> dict[str, Any]:
        """Load merged skill secrets from user and shared configs."""
        return self._skill_repo.load_merged_skill_secrets(user_id)

    def get_missing_skill_requirements(self, user_id: str) -> dict[str, dict[str, list[dict]]]:
        """Get missing skill requirements for a user.

        Returns historical return shape for unit tests that treat these as plain dicts.
        """
        missing = self._skill_repo.get_missing_skill_requirements(user_id)
        return cast(dict[str, dict[str, list[dict]]], missing)

    def get_user_working_dir(self, user_id: str) -> Path:
        """Get the working directory for a user.

        Args:
            user_id: User's telegram ID.

        Returns:
            Path to the user's working directory.
        """
        if self.file_service is None:
            # Fallback if file service is not available
            base = getattr(self.config, "working_folder_base_dir", "/tmp/mordecai_work")
            return Path(base) / user_id
        return self.file_service.get_user_working_dir(user_id)

    def build_system_prompt(
        self,
        user_id: str,
        memory_context: MemoryContext | None = None,
        attachments: list[AttachmentInfo] | None = None,
    ) -> str:
        """Build the system prompt for a user.

        Args:
            user_id: User's telegram ID.
            memory_context: Optional memory context to include.
            attachments: Optional attachment info to include.

        Returns:
            Complete system prompt string.
        """
        return self._prompt_builder.build(
            user_id=user_id,
            memory_context=memory_context,
            attachments=attachments,
        )

    def create_agent(
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
        model = self.create_model()
        user_skills_dir = str(self.get_user_skills_dir(user_id))

        # Get or create conversation manager for this user
        # This ensures conversation history is preserved across messages
        conversation_manager = self.get_or_create_conversation_manager(user_id)

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

        # Built-in Strands tools (not the same thing as instruction-based "skills").
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
            system_prompt=self.build_system_prompt(user_id, memory_context, attachments),
        )

        # Log loaded skills for this user
        logger.info(
            "User %s: Skills dir=%s, shared_dir=%s",
            user_id,
            user_skills_dir,
            self.config.shared_skills_dir,
        )
        skills = self.discover_skills(user_id)
        if skills:
            skill_names = [s["name"] for s in skills]
            logger.info(
                "User %s: Loaded %d skills: %s", user_id, len(skills), ", ".join(skill_names)
            )
        else:
            logger.info("User %s: No skills loaded", user_id)

        # Cache the agent so we can retrieve messages later
        self.cache_agent(user_id, agent)

        return agent

    def reload_agent(self, user_id: str) -> Agent:
        """Reload (clear and recreate) an agent for a user.

        Args:
            user_id: User's telegram ID.

        Returns:
            New Agent instance.
        """
        # Clear cached agent and conversation manager
        if user_id in self._user_agents:
            del self._user_agents[user_id]
        if user_id in self._user_conversation_managers:
            del self._user_conversation_managers[user_id]

        return self.create_agent(user_id)
