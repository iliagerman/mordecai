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

import logging
from typing import TYPE_CHECKING, Any

from app.config import AgentConfig
from app.enums import ModelProvider
from app.models.agent import AttachmentInfo, ConversationMessage, MemoryContext
from app.services.agent.agent_creation import AgentCreator
from app.services.agent.attachment_handler import AttachmentHandler

# Other components
from app.services.agent.explicit_memory import (
    ExplicitMemoryWriter,
)
from app.services.agent.message_processing import MessageProcessor
from app.services.agent.model_factory import ModelFactory
from app.services.agent.prompt_builder import SystemPromptBuilder

# Helper modules
from app.services.agent.session_management import SessionLifecycleManager
from app.services.agent.skills import SkillRepository

# State managers
from app.services.agent.state import (
    AgentNameRegistry,
    ExtractionLockRegistry,
    MessageCounter,
    SessionManager,
    StmCache,
)
from app.services.agent.state import (
    ConversationHistory as ConversationHistoryState,
)
from app.services.agent.test_skill_runner import DeterministicEchoSkillRunner
from app.services.personality_service import PersonalityService

if TYPE_CHECKING:
    from strands import Agent
    from strands.agent.conversation_manager import SlidingWindowConversationManager
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

        # State managers for type-safe internal state
        self._session_manager = SessionManager()
        self._agent_name_registry = AgentNameRegistry()
        self._conversation_history_state = ConversationHistoryState()
        self._message_counter = MessageCounter()
        self._extraction_lock = ExtractionLockRegistry()
        self._obsidian_stm_cache = StmCache()

        # Cached agent instances and conversation managers (third-party types)
        self._user_agents: dict[str, Agent] = {}
        self._user_conversation_managers: dict[
            str, SlidingWindowConversationManager
        ] = {}

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
            user_agent_names=self._agent_name_registry,
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

        # Initialize helper modules with required dependencies
        self._session_lifecycle = SessionLifecycleManager(
            config=config,
            extraction_service=extraction_service,
            memory_service=memory_service,
            file_service=file_service,
            session_manager=self._session_manager,
            agent_name_registry=self._agent_name_registry,
            conversation_history=self._conversation_history_state,
            message_counter=self._message_counter,
            extraction_lock=self._extraction_lock,
            obsidian_stm_cache=self._obsidian_stm_cache,
            user_conversation_managers=self._user_conversation_managers,
            user_agents=self._user_agents,
            get_conversation_history=self._get_conversation_history,
            create_agent=self._create_agent,
        )

        self._agent_creator = AgentCreator(
            config=config,
            memory_service=memory_service,
            cron_service=cron_service,
            file_service=file_service,
            pending_skill_service=pending_skill_service,
            skill_service=skill_service,
            session_manager=self._session_manager,
            skill_repo=self._skill_repo,
            model_factory=self._model_factory,
            prompt_builder=self._prompt_builder,
            user_conversation_managers=self._user_conversation_managers,
            user_agents=self._user_agents,
            get_session_id=self._get_session_id,
            on_agent_name_changed=self._on_agent_name_changed,
        )

        self._message_processor = MessageProcessor(
            config=config,
            memory_service=memory_service,
            logging_service=logging_service,
            conversation_history=self._conversation_history_state,
            message_counter=self._message_counter,
            extraction_lock=self._extraction_lock,
            get_session_id=self._get_session_id,
            get_user_messages=self._agent_creator.get_user_messages,
            create_agent=self._agent_creator.create_agent,
            add_to_conversation_history=self._add_to_conversation_history,
            sync_shared_skills=self._agent_creator.sync_shared_skills_for_user,
            increment_message_count=self._session_lifecycle.increment_message_count,
            maybe_store_explicit_memory=self._maybe_store_explicit_memory_request,
            trigger_extraction_and_clear=self._session_lifecycle.trigger_extraction_and_clear,
            deterministic_skill_runner=self._deterministic_skill_runner,
        )

        self._attachment_handler = AttachmentHandler(
            config=config,
            memory_service=memory_service,
            conversation_history=self._conversation_history_state,
            message_counter=self._message_counter,
            sync_shared_skills=self._agent_creator.sync_shared_skills_for_user,
            increment_message_count=self._session_lifecycle.increment_message_count,
            add_to_conversation_history=self._add_to_conversation_history,
            create_model=self._agent_creator.create_model,
            build_system_prompt=self._build_system_prompt,
        )

    # ========================================================================
    # Session Management Methods (delegated to SessionLifecycleManager)
    # ========================================================================

    def _get_session_id(self, user_id: str) -> str:
        """Get or create a session ID for a user."""
        return self._session_lifecycle.get_session_id(user_id)

    def increment_message_count(self, user_id: str, count: int = 1) -> int:
        """Increment and return the message count for a user."""
        return self._session_lifecycle.increment_message_count(user_id, count)

    def get_message_count(self, user_id: str) -> int:
        """Get current message count for a user."""
        return self._session_lifecycle.get_message_count(user_id)

    def reset_message_count(self, user_id: str) -> None:
        """Reset message count to zero for a user."""
        return self._session_lifecycle.reset_message_count(user_id)

    async def new_session(self, user_id: str) -> tuple["Agent", str]:
        """Create a fresh session with extraction before clearing."""
        return await self._session_lifecycle.new_session(user_id)

    async def _trigger_extraction_and_clear(self, user_id: str) -> None:
        """Trigger extraction and clear session (non-blocking)."""
        await self._session_lifecycle.trigger_extraction_and_clear(user_id)

    async def consolidate_short_term_memories_daily(self) -> None:
        """Internal daily job: promote Obsidian short-term memories into LTM."""
        await self._session_lifecycle.consolidate_short_term_memories_daily()

    def _clear_session_memory(self, user_id: str) -> None:
        """Clear session memory for a user."""
        self._session_lifecycle._clear_session_memory(user_id)

    # ========================================================================
    # Agent Creation Methods (delegated to AgentCreator)
    # ========================================================================

    def _create_model(self, use_vision: bool = False) -> "Model":
        """Create a model instance."""
        return self._agent_creator.create_model(use_vision=use_vision)

    def _get_or_create_conversation_manager(self, user_id: str) -> "SlidingWindowConversationManager":
        """Get or create a conversation manager for a user."""
        return self._agent_creator.get_or_create_conversation_manager(user_id)

    def _get_user_messages(self, user_id: str) -> list[Any]:
        """Get the cached messages for a user's session."""
        return self._agent_creator.get_user_messages(user_id)

    def _cache_agent(self, user_id: str, agent: "Agent") -> None:
        """Cache an agent instance for a user."""
        self._agent_creator.cache_agent(user_id, agent)

    def _discover_skills(self, user_id: str) -> list[dict]:
        """Discover installed instruction-based skills for a user."""
        return self._agent_creator.discover_skills(user_id)

    def _get_user_skills_dir(self, user_id: str):
        """Get the skills directory for a specific user."""
        return self._agent_creator.get_user_skills_dir(user_id)

    def _sync_shared_skills_for_user(self, user_id: str):
        """Sync shared skills into the user's skills directory."""
        return self._agent_creator.sync_shared_skills_for_user(user_id)

    def _sync_shared_skills(self, user_dir) -> None:
        """Mirror shared skills into a user's directory."""
        self._skill_repo.sync_shared_skills(user_dir)

    def _load_merged_skill_secrets(self, user_id: str) -> dict[str, Any]:
        """Load merged skill secrets from user and shared configs."""
        return self._agent_creator.load_merged_skill_secrets(user_id)

    def _get_missing_skill_requirements(self, user_id: str) -> dict[str, dict[str, list[dict]]]:
        """Get missing skill requirements for a user."""
        return self._agent_creator.get_missing_skill_requirements(user_id)

    def _get_user_working_dir(self, user_id: str):
        """Get the working directory for a user."""
        return self._agent_creator.get_user_working_dir(user_id)

    def _build_system_prompt(
        self,
        user_id: str,
        memory_context: MemoryContext | None = None,
        attachments: list[AttachmentInfo] | None = None,
    ) -> str:
        """Build the system prompt for the agent."""
        return self._agent_creator.build_system_prompt(
            user_id=user_id,
            memory_context=memory_context,
            attachments=attachments,
        )

    def _create_agent(
        self,
        user_id: str,
        memory_context: MemoryContext | None = None,
        attachments: list[AttachmentInfo] | None = None,
        messages: list[Any] | None = None,
    ) -> "Agent":
        """Create an agent instance."""
        return self._agent_creator.create_agent(
            user_id=user_id,
            memory_context=memory_context,
            attachments=attachments,
            messages=messages,
        )

    def get_or_create_agent(self, user_id: str) -> "Agent":
        """Create an agent for user."""
        return self._agent_creator.get_or_create_agent(user_id)

    def reload_agent(self, user_id: str) -> "Agent":
        """Reload agent to pick up new skills."""
        return self._agent_creator.reload_agent(user_id)

    def get_user_skills_directory(self, user_id: str) -> str:
        """Get the skills directory path for a user."""
        return str(self._agent_creator.get_user_skills_dir(user_id))

    # ========================================================================
    # Message Processing Methods (delegated to MessageProcessor)
    # ========================================================================

    async def process_message(self, user_id: str, message: str) -> str:
        """Process a user message through the agent."""
        return await self._message_processor.process_message(user_id, message)

    async def process_message_with_attachments(
        self,
        user_id: str,
        message: str,
        attachments: list[AttachmentInfo],
    ) -> str:
        """Process a message with file attachments."""
        return await self._message_processor.process_message_with_attachments(
            user_id=user_id,
            message=message,
            attachments=attachments,
        )

    def _maybe_run_simple_skill_echo_for_tests(self, *, user_id: str, message: str) -> str | None:
        """Pytest-only: run a simple echo-only skill deterministically."""
        return self._message_processor._maybe_run_simple_skill_echo_for_tests(
            user_id=user_id,
            message=message,
        )

    def _extract_response_text(self, result: Any) -> str:
        """Extract text response from agent result."""
        return self._message_processor._extract_response_text(result)

    # ========================================================================
    # Attachment Handling Methods (delegated to AttachmentHandler)
    # ========================================================================

    async def process_image_message(
        self,
        user_id: str,
        message: str,
        image_path: str,
    ) -> str:
        """Process a message with an image attachment."""
        return await self._attachment_handler.process_image_message(
            user_id=user_id,
            message=message,
            image_path=image_path,
        )

    def _get_media_type_from_extension(self, file_path: str) -> str:
        """Determine media type from file extension."""
        return self._attachment_handler.get_media_type_from_extension(file_path)

    def _prepare_image_content(
        self,
        image_path: str,
        caption: str | None = None,
    ) -> list[dict]:
        """Prepare image content for vision model input."""
        return self._attachment_handler.prepare_image_content(image_path, caption)

    # ========================================================================
    # Agent Name Management
    # ========================================================================

    def set_agent_name(self, user_id: str, name: str) -> None:
        """Set the agent name for a user (cached, caller saves to DB)."""
        self._agent_name_registry.set(user_id, name)
        logger.info("Set agent name for user %s: %s", user_id, name)

    def _on_agent_name_changed(self, user_id: str, name: str) -> None:
        """Callback when agent name is changed via tool.

        Updates the cached agent name.
        """
        self._agent_name_registry.set(user_id, name)
        logger.info("Agent name changed for user %s: %s", user_id, name)

    def get_agent_name(self, user_id: str) -> str | None:
        """Get the cached agent name for a user."""
        return self._agent_name_registry.get(user_id)

    # ========================================================================
    # Conversation History Management
    # ========================================================================

    def _get_conversation_history(self, user_id: str) -> list[ConversationMessage]:
        """Get conversation history for a user."""
        return self._conversation_history_state.get(user_id)

    def _add_to_conversation_history(self, user_id: str, role: str, content: str) -> None:
        """Add a message to the conversation history."""
        self._conversation_history_state.add_message(user_id, role, content)

    # ========================================================================
    # Memory Management
    # ========================================================================

    def _maybe_store_explicit_memory_request(self, user_id: str, message: str) -> None:
        """Best-effort immediate memory write for explicit 'remember' requests."""
        self._explicit_memory_writer.maybe_store(user_id=user_id, message=message)

    def _extract_explicit_memory_text(self, message: str) -> tuple[str, str] | None:
        """Extract explicit memory text from a message."""
        from app.services.agent.explicit_memory import extract_explicit_memory_text
        return extract_explicit_memory_text(message)

    def _contains_sensitive_memory_text(self, text: str) -> bool:
        """Reject likely secrets/PII from being stored via explicit remember."""
        from app.services.agent.explicit_memory import contains_sensitive_memory_text
        return contains_sensitive_memory_text(text)

    # ========================================================================
    # Public API Methods
    # ========================================================================

    def get_model_provider(self) -> ModelProvider:
        """Get the currently configured model provider."""
        return self.config.model_provider

    def cleanup_user(self, user_id: str) -> None:
        """Clean up resources for a user."""
        self._conversation_history_state.remove(user_id)
        self._agent_name_registry.remove(user_id)
        self._message_counter.remove(user_id)
        self._extraction_lock.release(user_id)
        self._user_conversation_managers.pop(user_id, None)
        self._user_agents.pop(user_id, None)

    def get_session_id(self, user_id: str) -> str | None:
        """Get the current session ID for a user."""
        return self._session_manager.get(user_id)

    # ========================================================================
    # Personality/Prompt Building (kept for direct access)
    # ========================================================================

    def _build_personality_section(self, user_id: str) -> str:
        """Build the personality section of the system prompt."""
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

    def _build_commands_section(self) -> str:
        """Build the commands section of the system prompt."""
        commands = getattr(self.config, "agent_commands", None)
        if not commands:
            return ""

        lines = ["## Available Commands\n"]
        for cmd in commands:
            name = cmd.get("name", "")
            desc = cmd.get("description", "")
            lines.append(f"- {name}: {desc}")
        lines.append("")
        return "\n".join(lines)
