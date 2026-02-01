"""Session management functionality for AgentService.

This module handles session lifecycle operations including:
- Creating new sessions with memory extraction
- Triggering extraction when conversation limit is reached
- Daily short-term memory consolidation
- Clearing session memory
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from app.models.agent import ConversationMessage

if TYPE_CHECKING:
    from strands import Agent
    from app.config import AgentConfig
    from app.services.memory_extraction_service import MemoryExtractionService
    from app.services.memory_service import MemoryService
    from app.services.file_service import FileService
    from app.services.agent.state import (
        SessionManager as SessionIdManager,
        AgentNameRegistry,
        ConversationHistory as ConversationHistoryState,
        MessageCounter,
        ExtractionLockRegistry,
        StmCache,
    )

logger = logging.getLogger(__name__)


class SessionLifecycleManager:
    """Handles session lifecycle operations."""

    def __init__(
        self,
        config: AgentConfig,
        extraction_service: MemoryExtractionService | None,
        memory_service: MemoryService | None,
        file_service: FileService | None,
        session_manager: SessionIdManager,
        agent_name_registry: AgentNameRegistry,
        conversation_history: ConversationHistoryState,
        message_counter: MessageCounter,
        extraction_lock: ExtractionLockRegistry,
        obsidian_stm_cache: StmCache,
        user_conversation_managers: dict,
        user_agents: dict,
        get_conversation_history: callable,
        create_agent: callable,
    ):
        """Initialize the session manager.

        Args:
            config: Application configuration.
            extraction_service: Optional MemoryExtractionService.
            memory_service: Optional MemoryService.
            file_service: Optional FileService.
            session_manager: Session ID manager.
            agent_name_registry: Agent name registry.
            conversation_history: Conversation history tracker.
            message_counter: Message counter.
            extraction_lock: Extraction lock registry.
            obsidian_stm_cache: Short-term memory cache.
            user_conversation_managers: Dict of user conversation managers.
            user_agents: Dict of user agents.
            get_conversation_history: Function to get conversation history.
            create_agent: Function to create a new agent.
        """
        self.config = config
        self.extraction_service = extraction_service
        self.memory_service = memory_service
        self.file_service = file_service
        self._session_manager = session_manager
        self._agent_name_registry = agent_name_registry
        self._conversation_history = conversation_history
        self._message_counter = message_counter
        self._extraction_lock = extraction_lock
        self._obsidian_stm_cache = obsidian_stm_cache
        self._user_conversation_managers = user_conversation_managers
        self._user_agents = user_agents
        self._get_conversation_history = get_conversation_history
        self._create_agent = create_agent

    def get_session_id(self, user_id: str) -> str:
        """Get or create a session ID for a user."""
        return self._session_manager.get_or_create(user_id)

    def increment_message_count(self, user_id: str, count: int = 1) -> int:
        """Increment and return the message count for a user.

        Args:
            user_id: User's telegram ID.
            count: Number to increment by (default 1).

        Returns:
            Updated message count for the user.
        """
        return self._message_counter.increment(user_id, count)

    def get_message_count(self, user_id: str) -> int:
        """Get current message count for a user.

        Args:
            user_id: User's telegram ID.

        Returns:
            Current message count (0 if user has no messages).
        """
        return self._message_counter.get(user_id)

    def reset_message_count(self, user_id: str) -> None:
        """Reset message count to zero for a user.

        Args:
            user_id: User's telegram ID.
        """
        self._message_counter.reset(user_id)

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
        session_id = self.get_session_id(user_id)

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
        # then clear stm.md on disk. This implements an explicit "handoff" model:
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

    async def trigger_extraction_and_clear(self, user_id: str) -> None:
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
        self._extraction_lock.acquire(user_id)

        try:
            session_id = self.get_session_id(user_id)
            conversation_history = self._get_conversation_history(user_id)
            history_for_extraction = self._conversation_history.to_dict_list(user_id)

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
            self._extraction_lock.release(user_id)

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

    def _clear_session_memory(self, user_id: str) -> None:
        """Clear session memory for a user.

        Args:
            user_id: User's telegram ID.
        """
        # Clear conversation history
        # Keep the key present but reset to an empty list. Some unit tests
        # assert the cleared state is `[]` (not missing/None).
        self._conversation_history.clear(user_id)

        # Clear conversation manager to reset agent's memory
        if user_id in self._user_conversation_managers:
            del self._user_conversation_managers[user_id]

        # Clear cached agent instance
        if user_id in self._user_agents:
            del self._user_agents[user_id]

        # Create new session ID
        self._session_manager.create_new(user_id)

        logger.info("Cleared session memory for user %s", user_id)

    def cleanup_user(self, user_id: str) -> None:
        """Clean up resources for a user."""
        self._conversation_history.remove(user_id)
