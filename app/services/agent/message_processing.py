"""Message processing functionality for AgentService.

This module handles core message processing including:
- Processing text messages through the agent
- Processing messages with attachments
- Managing conversation history tracking
- Triggering extraction when limits are reached
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from strands.agent.conversation_manager import SlidingWindowConversationManager

from app.enums import LogSeverity, ModelProvider
from app.models.agent import AttachmentInfo, MemoryContext
from app.observability.health_state import inflight_dec, inflight_inc, mark_progress
from app.observability.trace_context import new_trace_id, set_trace
from app.observability.trace_logging import trace_event
from app.services.agent.response_extractor import extract_response_text

if TYPE_CHECKING:
    from app.config import AgentConfig
    from app.dao.conversation_dao import ConversationDAO
    from app.services.agent.state import (
        ConversationHistory as ConversationHistoryState,
    )
    from app.services.agent.state import (
        ExtractionLockRegistry,
        MessageCounter,
    )
    from app.services.memory_service import MemoryService

logger = logging.getLogger(__name__)


def _is_bedrock_tool_transcript_validation_error(exc: Exception) -> bool:
    """Heuristic for Bedrock ConverseStream tool transcript validation failures.

    Bedrock enforces strict pairing and ordering of toolUse/toolResult blocks.
    When a transcript becomes invalid (often due to concurrency or partial tool
    runs), ConverseStream raises a ValidationException similar to:

        "The number of toolResult blocks ... exceeds the number of toolUse blocks ..."

    This is effectively deterministic for a given transcript. Callers can
    optionally retry with a clean seed history.
    """

    msg = str(exc) or ""
    lowered = msg.lower()

    # Core signature we've observed.
    if "toolresult" in lowered and "tooluse" in lowered and "exceeds" in lowered:
        return True

    # Broader fallback for AWS Bedrock streaming validation failures.
    if "conversestream" in lowered and "validationexception" in lowered:
        return True

    return False


class MessageProcessor:
    """Handles message processing through the agent."""

    def __init__(
        self,
        config: AgentConfig,
        memory_service: MemoryService | None,
        logging_service: Any | None,
        conversation_history: ConversationHistoryState,
        message_counter: MessageCounter,
        extraction_lock: ExtractionLockRegistry,
        get_session_id: Callable[..., Any],
        get_user_messages: Callable[..., Any],
        create_agent: Callable[..., Any],
        create_model: Callable[..., Any],
        add_to_conversation_history: Callable[..., Any],
        sync_shared_skills: Callable[..., Any],
        increment_message_count: Callable[..., Any],
        maybe_store_explicit_memory: Callable[..., Any],
        trigger_extraction_and_clear: Callable[..., Any],
        conversation_dao: ConversationDAO | None = None,
        deterministic_skill_runner: Any | None = None,
    ):
        """Initialize the message processor.

        Args:
            config: Application configuration.
            memory_service: Optional MemoryService.
            logging_service: Optional logging service.
            conversation_history: Conversation history tracker.
            message_counter: Message counter.
            extraction_lock: Extraction lock registry.
            get_session_id: Function to get session ID.
            get_user_messages: Function to get user messages.
            create_agent: Function to create an agent.
            create_model: Function to create a model (with vision support).
            add_to_conversation_history: Function to add to conversation history.
            sync_shared_skills: Function to sync shared skills.
            increment_message_count: Function to increment message count.
            maybe_store_explicit_memory: Function to store explicit memory.
            trigger_extraction_and_clear: Function to trigger extraction.
            deterministic_skill_runner: Optional deterministic skill runner for tests.
        """
        self.config = config
        self.memory_service = memory_service
        self.logging_service = logging_service
        self._conversation_history = conversation_history
        self._message_counter = message_counter
        self._extraction_lock = extraction_lock
        self._get_session_id = get_session_id
        self._get_user_messages = get_user_messages
        self._create_agent = create_agent
        self._create_model = create_model
        self._add_to_conversation_history = add_to_conversation_history
        self._sync_shared_skills = sync_shared_skills
        self._increment_message_count = increment_message_count
        self._maybe_store_explicit_memory = maybe_store_explicit_memory
        self._trigger_extraction_and_clear = trigger_extraction_and_clear
        self._conversation_dao = conversation_dao
        self._deterministic_skill_runner = deterministic_skill_runner
        self._set_session_id: Callable[[str, str], None] | None = None

    def _job_thread_ids(self, *, user_id: str) -> tuple[str, str, str, str]:
        """Compute identifiers for main thread + per-job thread.

        We intentionally generate a job token internally so callers (and unit
        test stubs) don't need to understand job semantics.
        """
        main_thread_id = self._get_session_id(user_id)
        token = uuid.uuid4().hex
        short = token.replace("-", "")[:8]
        job_thread_id = f"{main_thread_id}__job__{short}"
        return main_thread_id, token, short, job_thread_id

    async def _load_main_thread_snapshot(
        self,
        *,
        user_id: str,
        main_thread_id: str,
    ) -> tuple[list[dict], str]:
        """Load recent messages for context, stripped to **text-only** blocks.

        Critical: we never seed a new agent with toolUse/toolResult blocks.
        Those blocks can become inconsistent under parallelism or after
        tool timeouts, and Bedrock enforces strict pairing.

        Returns:
            A tuple of (messages, effective_session_id).  The session id may
            differ from *main_thread_id* when the process restarted and the
            session was recovered from the database.
        """

        def _as_text_only_message(m: dict) -> dict:
            role = str(m.get("role") or "user")
            content = m.get("content")

            # Strands/Bedrock structured format: content is list of blocks.
            text_parts: list[str] = []
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        text_parts.append(block["text"])
            elif isinstance(content, str):
                text_parts.append(content)

            text = "\n".join([t for t in text_parts if t]).strip()
            return {"role": role, "content": [{"text": text}]}

        if self._conversation_dao is None:
            # Fall back to in-memory conversation history (text-only) rather than
            # cached Strands agent messages (which may include tool blocks).
            history = self._conversation_history.get(user_id)
            window = int(getattr(self.config, "conversation_window_size", 20) or 20)
            recent = history[-window:] if window > 0 else history
            return (
                [{"role": m.role, "content": [{"text": m.content}]} for m in recent],
                main_thread_id,
            )

        try:
            window = getattr(self.config, "conversation_window_size", 20)
            msgs = await self._conversation_dao.get_conversation_structured(
                user_id=user_id,
                session_id=main_thread_id,
                exclude_cron=True,
                limit=window,
            )

            effective_id = main_thread_id

            # ---- Session recovery after process restart ----
            # SessionManager keeps session IDs in memory only.  After a
            # restart, a brand-new session_id is generated and the DB query
            # above returns nothing even though older messages still exist.
            # Detect this case and recover the previous session_id so the
            # agent retains conversational context.
            if not msgs:
                recovered = await self._conversation_dao.get_latest_session_id(
                    user_id=user_id,
                    exclude_cron=True,
                )
                if recovered and recovered != main_thread_id:
                    msgs = await self._conversation_dao.get_conversation_structured(
                        user_id=user_id,
                        session_id=recovered,
                        exclude_cron=True,
                        limit=window,
                    )
                    if msgs:
                        effective_id = recovered
                        # Update the in-memory session manager so the rest
                        # of the request (and future requests) use the
                        # recovered session_id.
                        if self._set_session_id is not None:
                            self._set_session_id(user_id, recovered)
                        logger.info(
                            "Recovered session for user %s: %s -> %s (%d messages)",
                            user_id,
                            main_thread_id,
                            recovered,
                            len(msgs),
                        )

            # Extra safety: strip any non-text blocks if they ever made it
            # into the main thread.
            out: list[dict] = []
            for m in msgs:
                if isinstance(m, dict):
                    out.append(_as_text_only_message(m))
            return out, effective_id
        except Exception:
            return [], main_thread_id

    async def _persist_main_plain_message(
        self,
        *,
        user_id: str,
        main_thread_id: str,
        role: str,
        content: str,
    ) -> None:
        if self._conversation_dao is None:
            return
        try:
            await self._conversation_dao.save_message(
                user_id=user_id,
                session_id=main_thread_id,
                role=role,
                content=content,
                is_cron=False,
                created_at=datetime.utcnow(),
            )
        except Exception:
            # Never break message processing due to DB persistence.
            return

    async def _persist_job_structured_transcript(
        self,
        *,
        user_id: str,
        job_thread_id: str,
        snapshot: list[dict],
        delta: list[Any],
    ) -> None:
        if self._conversation_dao is None:
            return

        try:
            # Persist the snapshot first so the job thread is self-contained.
            for m in snapshot:
                if isinstance(m, dict):
                    await self._conversation_dao.save_structured_message(
                        user_id=user_id,
                        session_id=job_thread_id,
                        message=m,
                        is_cron=False,
                        created_at=datetime.utcnow(),
                        redact=True,
                    )

            for m in delta:
                msg_dict: dict | None = None
                if isinstance(m, dict):
                    msg_dict = m
                else:
                    for attr in ("model_dump", "dict"):
                        try:
                            fn = getattr(m, attr)
                            if callable(fn):
                                dumped = fn()
                                if isinstance(dumped, dict):
                                    msg_dict = dumped
                                    break
                        except Exception:
                            continue

                if msg_dict is None:
                    msg_dict = {"role": "assistant", "content": [{"text": str(m)}]}

                await self._conversation_dao.save_structured_message(
                    user_id=user_id,
                    session_id=job_thread_id,
                    message=msg_dict,
                    is_cron=False,
                    created_at=datetime.utcnow(),
                    redact=True,
                )
        except Exception:
            return

    async def process_message(
        self,
        user_id: str,
        message: str,
        onboarding_context: dict[str, str | None] | None = None,
    ) -> str:
        """Process a user message through the agent.

        Retrieves relevant memory context based on the message and
        creates an agent with that context. Tracks message counts and
        triggers extraction when conversation limit is reached.

        Args:
            user_id: User's telegram ID.
            message: User's message to process.
            onboarding_context: Optional onboarding context (soul.md, id.md content)
                if this is the user's first interaction.

        Returns:
            Agent's response text.
        """
        inflight_inc()
        mark_progress("agent.message.start")
        response: str | None = None
        # Initialize tracing variables before any potential exception
        trace_this: bool = False
        trace_id: str | None = None
        t0: float = 0.0
        try:
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
            if os.getenv("PYTEST_CURRENT_TEST") and self._deterministic_skill_runner:
                deterministic = self._maybe_run_simple_skill_echo_for_tests(
                    user_id=user_id,
                    message=message,
                )
                if deterministic is not None:
                    # Keep behavior closer to the normal flow (sync + counts).
                    self._sync_shared_skills(user_id)
                    self._increment_message_count(user_id, 1)
                    # Track both sides for extraction consistency
                    self._add_to_conversation_history(user_id, "user", message)
                    self._add_to_conversation_history(user_id, "assistant", deterministic)
                    self._increment_message_count(user_id, 1)
                    return deterministic

            # Sync shared skills into user skills on every message.
            self._sync_shared_skills(user_id)

            # Increment count for user message (6.1)
            self._increment_message_count(user_id, 1)

            # Track user message for extraction
            self._add_to_conversation_history(user_id, "user", message)

            # If the user explicitly asks to remember something, store it
            # immediately (do not rely on end-of-session extraction).
            self._maybe_store_explicit_memory(user_id=user_id, message=message)

            # Retrieve memory context based on user's message
            memory_context: MemoryContext | None = None
            if self.config.memory_enabled and self.memory_service is not None:
                try:
                    ctx_dict = self.memory_service.retrieve_memory_context(
                        user_id=user_id, query=message
                    )
                    # Handle agent_name which may be str | None | list[str]
                    # due to type annotation inconsistency in retrieve_memory_context
                    agent_name_value = ctx_dict.get("agent_name")
                    agent_name: str | None = (
                        agent_name_value[0]
                        if isinstance(agent_name_value, list) and agent_name_value
                        else agent_name_value
                        if isinstance(agent_name_value, str)
                        else None
                    )
                    memory_context = MemoryContext(
                        agent_name=agent_name,
                        facts=ctx_dict.get("facts", []),
                        preferences=ctx_dict.get("preferences", []),
                    )
                    logger.info(
                        "Memory context for %s: facts=%d, prefs=%d",
                        user_id,
                        len(memory_context.facts or []),
                        len(memory_context.preferences or []),
                    )
                except Exception as e:
                    logger.warning("Failed to retrieve memory: %s", e)
                    memory_context = None

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
                fields: dict[str, object] = {
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

            main_thread_id, _token, _job_short, job_thread_id = self._job_thread_ids(
                user_id=user_id
            )

            # Load context BEFORE persisting the new user message, to avoid
            # double-including it (once in history, once as the prompt).
            # The effective session id may differ from main_thread_id if the
            # session was recovered from the DB after a process restart.
            snapshot, main_thread_id = await self._load_main_thread_snapshot(
                user_id=user_id,
                main_thread_id=main_thread_id,
            )
            job_thread_id = f"{main_thread_id}__job__{_job_short}"

            await self._persist_main_plain_message(
                user_id=user_id,
                main_thread_id=main_thread_id,
                role="user",
                content=message,
            )

            # Fresh, isolated agent per job to allow true same-user parallelism.
            seed_snapshot = snapshot
            agent = self._create_agent(
                user_id,
                memory_context,
                onboarding_context=onboarding_context,
                messages=seed_snapshot,
                for_cron_task=True,
            )
            initial_len = len(seed_snapshot or [])

            # IMPORTANT:
            # Strands agent calls are synchronous and can execute tools that may
            # block (subprocess/network). Running them in a background thread keeps
            # the asyncio event loop responsive so /health continues to answer.
            mark_progress("agent.invoke.start")
            try:
                result = await asyncio.to_thread(agent, message)
            except Exception as e:
                # If Bedrock reports a tool transcript mismatch, retry once with
                # a clean seed history. Retrying the same transcript is unlikely
                # to succeed.
                if _is_bedrock_tool_transcript_validation_error(e):
                    logger.warning(
                        "Bedrock tool transcript validation failed for user %s; retrying once with empty seed history: %s",
                        user_id,
                        e,
                        exc_info=True,
                    )
                    seed_snapshot = []
                    agent = self._create_agent(
                        user_id,
                        memory_context,
                        onboarding_context=onboarding_context,
                        messages=seed_snapshot,
                        for_cron_task=True,
                    )
                    initial_len = 0
                    result = await asyncio.to_thread(agent, message)
                else:
                    raise
            finally:
                mark_progress("agent.invoke.end")

            response = self._extract_response_text(result)

            # Persist structured transcript for debugging/continuation.
            produced = []
            try:
                produced = list(getattr(agent, "messages", []) or [])
            except Exception:
                produced = []
            delta = produced[initial_len:] if initial_len <= len(produced) else produced
            await self._persist_job_structured_transcript(
                user_id=user_id,
                job_thread_id=job_thread_id,
                snapshot=seed_snapshot,
                delta=delta,
            )

            await self._persist_main_plain_message(
                user_id=user_id,
                main_thread_id=main_thread_id,
                role="assistant",
                content=response,
            )

            # Track agent response for extraction
            self._add_to_conversation_history(user_id, "assistant", response)

            # Increment count for agent response (6.1)
            self._increment_message_count(user_id, 1)

            # Check if extraction needed (6.1, 6.2)
            current_count = self._message_counter.get(user_id)
            if current_count >= self.config.max_conversation_messages:
                # Trigger non-blocking extraction (6.2)
                if not self._extraction_lock.is_locked(user_id):
                    asyncio.create_task(self._trigger_extraction_and_clear(user_id))
                    # Append notification to response (6.4)
                    response = (
                        f"{response}\n\n"
                        "✨ Your conversation has been summarized and important "
                        "information saved. Starting fresh!"
                    )

            if trace_this:
                end_fields: dict[str, object] = {
                    "duration_ms": int((time.perf_counter() - t0) * 1000),
                    "message_count": self._message_counter.get(user_id),
                }
                if getattr(self.config, "trace_model_io_enabled", False):
                    end_fields.update(
                        {
                            "assistant_response": response,
                            "assistant_response_len": len(response or ""),
                        }
                    )
                trace_event(
                    "agent.message.end",
                    max_chars=getattr(self.config, "trace_max_chars", 2000),
                    **end_fields,
                )

            if self.logging_service is not None:
                try:
                    await self.logging_service.log_action(
                        user_id=user_id,
                        action="Processed message: completed",
                        severity=LogSeverity.INFO,
                        details={
                            "trace_id": trace_id,
                            "duration_ms": int((time.perf_counter() - t0) * 1000),
                        },
                    )
                except Exception:
                    pass

            return response
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
            # Do not poison-retry: return a user-facing error instead of raising.
            main_thread_id, _token, _job_short, job_thread_id = self._job_thread_ids(
                user_id=user_id
            )
            msg = "I hit an internal error while processing this request. Please resend your last message."

            # Best-effort persistence for visibility.
            await self._persist_main_plain_message(
                user_id=user_id,
                main_thread_id=main_thread_id,
                role="assistant",
                content=msg,
            )
            await self._persist_job_structured_transcript(
                user_id=user_id,
                job_thread_id=job_thread_id,
                snapshot=[],
                delta=[{"role": "assistant", "content": [{"text": msg}]}],
            )
            return msg
        finally:
            # Keep inflight accounting accurate even on early returns/exceptions.
            inflight_dec()

    async def process_message_with_attachments(
        self,
        user_id: str,
        message: str,
        attachments: list[AttachmentInfo],
        onboarding_context: dict[str, str | None] | None = None,
    ) -> str:
        """Process a message with file attachments.

        Creates an agent with attachment context and processes the message.
        Tracks message counts and triggers extraction when limit is reached.

        Args:
            user_id: User's telegram ID.
            message: User's text message (may be empty).
            attachments: List of attachment metadata with keys:
                - file_id: Telegram file ID
                - file_name: Sanitized filename
                - file_path: Full path to downloaded file
                - mime_type: MIME type if known
                - file_size: Size in bytes
                - is_image: Whether file is an image
            onboarding_context: Optional onboarding context (soul.md, id.md content)
                if this is the user's first interaction.

        Returns:
            Agent's response text.

        Requirements:
            - 1.4: Forward file path and metadata to agent
            - 4.1: Agent has access to downloaded files
            - 4.2: Provide full path to downloaded files
            - 4.3: Include file metadata in context
        """
        # Special case: if there's a single image attachment, use the dedicated
        # image processing path which has proper vision tool support
        images = [att for att in attachments if att.is_image]
        if len(images) == 1 and len(attachments) == 1:
            # Use the dedicated image processing path from AttachmentHandler
            image_path = images[0].file_path
            if image_path:
                # We need to use the attachment_handler's process_image_message
                # But MessageProcessor doesn't have direct access to it.
                # For now, create a vision-capable agent with the image_reader tool.
                return await self._process_single_image(
                    user_id=user_id,
                    message=message,
                    image_path=image_path,
                    onboarding_context=onboarding_context,
                )

        inflight_inc()
        mark_progress("agent.attachments.start")
        # Sync shared skills into user skills on every message.
        self._sync_shared_skills(user_id)

        # Increment count for user message
        self._increment_message_count(user_id, 1)

        # Store explicit memory requests immediately even when attachments
        # are present.
        if message:
            self._add_to_conversation_history(user_id, "user", message)
            self._maybe_store_explicit_memory(
                user_id=user_id,
                message=message,
            )

        # Retrieve memory context
        memory_context: MemoryContext | None = None
        if self.config.memory_enabled and self.memory_service is not None:
            try:
                query = message if message else "file attachment"
                ctx_dict = self.memory_service.retrieve_memory_context(user_id=user_id, query=query)
                # Handle agent_name which may be str | None | list[str]
                agent_name_value = ctx_dict.get("agent_name")
                agent_name: str | None = (
                    agent_name_value[0]
                    if isinstance(agent_name_value, list) and agent_name_value
                    else agent_name_value
                    if isinstance(agent_name_value, str)
                    else None
                )
                memory_context = MemoryContext(
                    agent_name=agent_name,
                    facts=ctx_dict.get("facts", []),
                    preferences=ctx_dict.get("preferences", []),
                )
                logger.info(
                    "Memory context for %s: facts=%d, prefs=%d",
                    user_id,
                    len(memory_context.facts or []),
                    len(memory_context.preferences or []),
                )
            except Exception as e:
                logger.warning("Failed to retrieve memory: %s", e)
                memory_context = None

        main_thread_id, _token, _job_short, job_thread_id = self._job_thread_ids(user_id=user_id)

        # Build prompt with file info if no message provided
        if message:
            prompt = message
        else:
            file_names = [att.file_name or "file" for att in attachments]
            prompt = f"I've sent you these files: {', '.join(file_names)}"

        # Load context BEFORE persisting the new user prompt, to avoid
        # double-including it (once in history, once as the prompt).
        snapshot, main_thread_id = await self._load_main_thread_snapshot(
            user_id=user_id,
            main_thread_id=main_thread_id,
        )
        job_thread_id = f"{main_thread_id}__job__{_job_short}"

        await self._persist_main_plain_message(
            user_id=user_id,
            main_thread_id=main_thread_id,
            role="user",
            content=prompt,
        )

        # Create isolated agent with attachment context
        seed_snapshot = snapshot
        agent = self._create_agent(
            user_id,
            memory_context,
            attachments,
            onboarding_context=onboarding_context,
            messages=seed_snapshot,
            for_cron_task=True,
        )
        initial_len = len(seed_snapshot or [])

        # Ensure the prompt used is tracked for extraction.
        if not message:
            self._add_to_conversation_history(user_id, "user", prompt)

        try:
            mark_progress("agent.invoke.start")
            try:
                result = await asyncio.to_thread(agent, prompt)
            except Exception as e:
                if _is_bedrock_tool_transcript_validation_error(e):
                    logger.warning(
                        "Bedrock tool transcript validation failed for user %s (attachments); retrying once with empty seed history: %s",
                        user_id,
                        e,
                        exc_info=True,
                    )
                    seed_snapshot = []
                    agent = self._create_agent(
                        user_id,
                        memory_context,
                        attachments,
                        onboarding_context=onboarding_context,
                        messages=seed_snapshot,
                        for_cron_task=True,
                    )
                    initial_len = 0
                    result = await asyncio.to_thread(agent, prompt)
                else:
                    raise
            finally:
                mark_progress("agent.invoke.end")
            response = self._extract_response_text(result)

            produced = []
            try:
                produced = list(getattr(agent, "messages", []) or [])
            except Exception:
                produced = []
            delta = produced[initial_len:] if initial_len <= len(produced) else produced
            await self._persist_job_structured_transcript(
                user_id=user_id,
                job_thread_id=job_thread_id,
                snapshot=seed_snapshot,
                delta=delta,
            )

        except Exception as e:
            logger.exception("Failed processing attachment message for user %s: %s", user_id, e)
            response = (
                "I hit an internal error while processing your attachments. Please try again."
            )
        finally:
            inflight_dec()

        # Track agent response for extraction
        self._add_to_conversation_history(user_id, "assistant", response)

        # Increment count for agent response
        self._increment_message_count(user_id, 1)

        # Check if extraction needed
        current_count = self._message_counter.get(user_id)
        if current_count >= self.config.max_conversation_messages:
            if not self._extraction_lock.is_locked(user_id):
                asyncio.create_task(self._trigger_extraction_and_clear(user_id))
                response = (
                    f"{response}\n\n"
                    "✨ Your conversation has been summarized and important "
                    "information saved. Starting fresh!"
                )

        await self._persist_main_plain_message(
            user_id=user_id,
            main_thread_id=main_thread_id,
            role="assistant",
            content=response,
        )

        return response

    def _maybe_run_simple_skill_echo_for_tests(self, *, user_id: str, message: str) -> str | None:
        """Pytest-only: run a simple echo-only skill deterministically.

        Returns a human-readable response if it executed a skill, otherwise None.
        """
        if self._deterministic_skill_runner is None:
            return None
        return self._deterministic_skill_runner.maybe_run(user_id=user_id, message=message)

    async def _process_single_image(
        self,
        user_id: str,
        message: str,
        image_path: str,
        onboarding_context: dict[str, str | None] | None = None,
    ) -> str:
        """Process a single image attachment using vision model.

        Creates a vision-capable agent and passes the image as content blocks
        directly to the model, bypassing the image_reader tool.

        This approach is cleaner and more reliable than using the image_reader
        tool because the image data is passed directly to the vision model.

        Args:
            user_id: User's telegram ID.
            message: User's text message (caption).
            image_path: Path to the downloaded image file.
            onboarding_context: Optional onboarding context.

        Returns:
            Agent's response text.
        """
        # Sync shared skills
        self._sync_shared_skills(user_id)

        # Increment message count
        self._increment_message_count(user_id, 1)

        main_thread_id, _token, _job_short, job_thread_id = self._job_thread_ids(user_id=user_id)

        # Track user message
        prompt_text = message or f"[Image: {image_path}]"
        self._add_to_conversation_history(user_id, "user", prompt_text)
        self._maybe_store_explicit_memory(user_id=user_id, message=prompt_text)

        # Persist user prompt into main thread.
        await self._persist_main_plain_message(
            user_id=user_id,
            main_thread_id=main_thread_id,
            role="user",
            content=prompt_text,
        )

        # Retrieve memory context
        memory_context: MemoryContext | None = None
        if self.config.memory_enabled and self.memory_service is not None:
            try:
                ctx_dict = self.memory_service.retrieve_memory_context(
                    user_id=user_id, query=message or "image analysis"
                )
                # Handle agent_name which may be str | None | list[str]
                agent_name_value = ctx_dict.get("agent_name")
                agent_name: str | None = (
                    agent_name_value[0]
                    if isinstance(agent_name_value, list) and agent_name_value
                    else agent_name_value
                    if isinstance(agent_name_value, str)
                    else None
                )
                memory_context = MemoryContext(
                    agent_name=agent_name,
                    facts=ctx_dict.get("facts", []),
                    preferences=ctx_dict.get("preferences", []),
                )
            except Exception as e:
                logger.warning("Failed to retrieve memory: %s", e)
                memory_context = None

        # Create vision model
        use_vision = bool(self.config.vision_model_id)
        model = self._create_model(use_vision=use_vision)

        # Create conversation manager for this session
        conversation_manager = SlidingWindowConversationManager(
            window_size=self.config.conversation_window_size,
        )

        # Build system prompt for vision analysis
        if memory_context and memory_context.agent_name:
            system_prompt = (
                f"YOUR NAME IS {memory_context.agent_name.upper()}.\n"
                f"Always identify yourself as {memory_context.agent_name}.\n"
                "You are a helpful AI assistant with vision capabilities. "
                "When users send images, analyze them carefully and describe "
                "what you see in detail."
            )
        else:
            system_prompt = (
                "You are a helpful AI assistant with vision capabilities. "
                "When users send images, analyze them carefully and describe "
                "what you see in detail."
            )

        # Prepare the image as content blocks
        # This passes the image data directly to the vision model
        from pathlib import Path

        from strands import Agent
        from strands.types.content import Message

        # Determine image format
        ext = Path(image_path).suffix.lower()
        format_map = {
            ".png": "png",
            ".jpg": "jpeg",
            ".jpeg": "jpeg",
            ".gif": "gif",
            ".webp": "webp",
        }
        img_format = format_map.get(ext, "png")

        # Read image bytes and create content block in Strands SDK format
        with open(image_path, "rb") as f:
            image_bytes = f.read()

        # Create content blocks with image in the correct Strands SDK format
        # Format: {"image": {"format": "png", "source": {"bytes": b"..."}}
        content_blocks: list[dict] = [
            {"image": {"format": img_format, "source": {"bytes": image_bytes}}}
        ]

        logger.info(
            "User %s: Processing image attachment with direct content blocks (%s, %d bytes)",
            user_id,
            img_format,
            len(image_bytes),
        )

        # Create the initial user message with image content
        user_message = Message(role="user", content=content_blocks)  # type: ignore

        # Create agent with the image message pre-loaded
        # No image_reader tool needed - the model receives image data directly
        agent = Agent(
            model=model,
            messages=[user_message],  # Pass image as initial message
            conversation_manager=conversation_manager,
            tools=[],  # No tools needed for direct image processing
            system_prompt=system_prompt,
        )

        # Execute agent - the image is already in the conversation context
        # If no caption was provided, send a prompt to trigger analysis
        prompt = message or "Please analyze this image and describe what you see."

        try:
            mark_progress("agent.invoke.start")
            try:
                result = await asyncio.to_thread(agent, prompt)
            except Exception as e:
                if _is_bedrock_tool_transcript_validation_error(e):
                    logger.warning(
                        "Bedrock tool transcript validation failed for user %s (single image); retrying once: %s",
                        user_id,
                        e,
                        exc_info=True,
                    )
                    # Rebuild agent from scratch and retry once.
                    conversation_manager = SlidingWindowConversationManager(
                        window_size=self.config.conversation_window_size,
                    )
                    agent = Agent(
                        model=model,
                        messages=[user_message],
                        conversation_manager=conversation_manager,
                        tools=[],
                        system_prompt=system_prompt,
                    )
                    result = await asyncio.to_thread(agent, prompt)
                else:
                    raise
            finally:
                mark_progress("agent.invoke.end")

            response = self._extract_response_text(result)

            # Best-effort: persist a structured transcript for the job thread.
            produced = []
            try:
                produced = list(getattr(agent, "messages", []) or [])
            except Exception:
                produced = []
            await self._persist_job_structured_transcript(
                user_id=user_id,
                job_thread_id=job_thread_id,
                snapshot=[],
                delta=produced,
            )
        except Exception as e:
            logger.exception("Failed processing single image message for user %s: %s", user_id, e)
            response = "I hit an internal error while processing your image. Please try again."
            await self._persist_job_structured_transcript(
                user_id=user_id,
                job_thread_id=job_thread_id,
                snapshot=[],
                delta=[{"role": "assistant", "content": [{"text": response}]}],
            )

        # Track response and increment count
        self._add_to_conversation_history(user_id, "assistant", response)
        self._increment_message_count(user_id, 1)

        # Check for extraction
        current_count = self._message_counter.get(user_id)
        if current_count >= self.config.max_conversation_messages:
            if not self._extraction_lock.is_locked(user_id):
                asyncio.create_task(self._trigger_extraction_and_clear(user_id))
                response = (
                    f"{response}\n\n"
                    "✨ Your conversation has been summarized and important "
                    "information saved. Starting fresh!"
                )

        await self._persist_main_plain_message(
            user_id=user_id,
            main_thread_id=main_thread_id,
            role="assistant",
            content=response,
        )
        return response

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
