"""Multi-agent conversation service.

This service manages conversations between multiple AI agents,
orchestrating round-robin discussions until consensus or max iterations.

Requirements:
- Create conversations with topic and max iterations
- Add/remove participants
- Wait for owner instructions before starting (configurable timeout)
- Execute round-robin turns with participating agents
- Detect agreement and end conditions
- Store conversation history
- Deliver transcripts to all participants when the conversation ends
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from app.enums import ConversationStatus
from app.models.domain import ParameterAnalysis

if TYPE_CHECKING:
    from app.dao.conversation_dao import ConversationDAO
    from app.services.conversation_manager_agent import ConversationManagerAgent

logger = logging.getLogger(__name__)

DEFAULT_INSTRUCTION_TIMEOUT = 300  # 5 minutes

# Callback: (chat_id: int, text: str) -> None
SendMessageCallback = Callable[..., Coroutine[Any, Any, None]]
# Callback: (user_id: str) -> int | None   (resolves username -> telegram chat_id)
ResolveChatIdCallback = Callable[[str], Coroutine[Any, Any, int | None]]


class ConversationService:
    """Service for managing multi-agent conversations.

    All updates are delivered as direct messages to participants.
    The turn cycle never blocks: if an agent's owner hasn't provided
    instructions yet the agent is skipped with a status message and
    the cycle moves on.  When the conversation ends a full transcript
    is sent to every user whose bot participated.
    """

    def __init__(
        self,
        conversation_dao: ConversationDAO,
        agent_service: Any,
        send_message: SendMessageCallback,
        resolve_chat_id: ResolveChatIdCallback,
        manager_agent: ConversationManagerAgent | None = None,
    ) -> None:
        self._conversation_dao = conversation_dao
        self._agent_service = agent_service
        self._send_message = send_message
        self._resolve_chat_id = resolve_chat_id
        self._manager_agent = manager_agent

        # In-memory state for active conversations
        self._active_conversations: dict[str, ConversationState] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_conversation(
        self,
        creator_user_id: str,
        creator_chat_id: int,
        topic: str,
        max_iterations: int = 5,
        participant_user_ids: list[str] | None = None,
        agent_instructions: str | None = None,
        instruction_timeout: int = DEFAULT_INSTRUCTION_TIMEOUT,
    ) -> str:
        """Create a new multi-agent conversation.

        The conversation waits up to ``instruction_timeout`` seconds for
        invited agents' owners to provide instructions.  Agents whose
        owners don't respond in time are excluded.

        Returns:
            The conversation ID.
        """
        conversation_id = await self._conversation_dao.create_conversation(
            creator_user_id=creator_user_id,
            topic=topic,
            max_iterations=max_iterations,
        )

        # Build full participant list: creator + invited agents
        all_participant_ids = [creator_user_id]
        if participant_user_ids:
            for uid in participant_user_ids:
                if uid != creator_user_id:
                    all_participant_ids.append(uid)

        for uid in all_participant_ids:
            await self._conversation_dao.add_participant(
                conversation_id=conversation_id,
                user_id=uid,
            )

        state = ConversationState(
            conversation_id=conversation_id,
            topic=topic,
            max_iterations=max_iterations,
            creator_user_id=creator_user_id,
            agent_instructions=agent_instructions,
            instruction_timeout=instruction_timeout,
        )
        state.known_chat_ids[creator_user_id] = creator_chat_id

        # All participants (including creator) start in "awaiting instructions".
        state.awaiting_instructions.update(all_participant_ids)

        self._active_conversations[conversation_id] = state

        logger.info(
            "Created conversation %s: topic='%s', max_iter=%d, timeout=%ds, agents=%s",
            conversation_id, topic, max_iterations, instruction_timeout,
            participant_user_ids or [],
        )

        timeout_minutes = instruction_timeout // 60
        timeout_label = (
            f"{timeout_minutes} minute{'s' if timeout_minutes != 1 else ''}"
            if instruction_timeout >= 60
            else f"{instruction_timeout} seconds"
        )

        # Notify each invited participant (excluding creator) so they can provide instructions
        for uid in all_participant_ids:
            if uid != creator_user_id:
                await self._send_to_user(
                    uid,
                    f"Your agent has been invited to a conversation.\n\n"
                    f"Topic: {topic}\n"
                    f"Conversation ID: {conversation_id}\n"
                    f"Max iterations: {max_iterations}\n\n"
                    f"You have {timeout_label} to provide instructions.\n"
                    f"Use: conversation instruct <your instructions>\n\n"
                    f"If you don't respond in time, your agent will not participate.",
                    state,
                )

        # Notify the creator — they also need to provide instructions
        other_agents = [uid for uid in all_participant_ids if uid != creator_user_id]
        await self._notify_creator(
            state,
            f"Conversation created! Waiting for all agents to provide instructions.\n\n"
            f"Topic: {topic}\n"
            f"Timeout: {timeout_label}\n"
            f"Other agents invited: {', '.join(other_agents) if other_agents else 'none'}\n\n"
            f"You also need to provide instructions for YOUR agent:\n"
            f"  conversation instruct <your instructions>\n\n"
            f"The conversation will start automatically when all agents respond "
            f"or when the timeout expires.",
        )

        # Schedule the start after the timeout
        self._schedule_start(conversation_id, instruction_timeout)

        return conversation_id

    async def add_participant(
        self,
        conversation_id: str,
        user_id: str,
        agent_name: str | None = None,
    ) -> bool:
        """Add a participant to an existing conversation.

        The new participant starts in "awaiting instructions" state.

        Returns:
            True if successful, False otherwise.
        """
        conv_data = await self._conversation_dao.get_conversation_by_id(conversation_id)
        if not conv_data or conv_data["status"] != ConversationStatus.ACTIVE:
            logger.warning("Cannot add participant to non-active conversation %s", conversation_id)
            return False

        success = await self._conversation_dao.add_participant(
            conversation_id=conversation_id,
            user_id=user_id,
            agent_name=agent_name,
        )
        if not success:
            return False

        state = self._active_conversations.get(conversation_id)
        if state:
            display = agent_name or user_id
            state.awaiting_instructions.add(user_id)

            await self._notify_creator(
                state, f"Agent '{display}' joined conversation {conversation_id}."
            )

            await self._send_to_user(
                user_id,
                f"Your agent has been invited to a conversation.\n\n"
                f"Topic: {state.topic}\n"
                f"Conversation ID: {conversation_id}\n"
                f"Max iterations: {state.max_iterations}\n\n"
                f"Provide instructions for your agent:\n"
                f"  conversation instruct <your instructions>\n\n"
                f"The conversation will continue while you decide. "
                f"Your agent will participate once you send instructions.",
                state,
            )

        return True

    async def handle_private_instruction(
        self,
        user_id: str,
        instruction: str,
        conversation_id: str | None = None,
    ) -> str | None:
        """Handle a private instruction from an agent's owner.

        Removes the agent from the "awaiting instructions" set so they
        will participate on their next turn.

        Returns:
            Response message or None if the user has no active conversation.
        """
        logger.info(
            "handle_private_instruction: user=%s, conversation_id=%s, instruction='%s'",
            user_id, conversation_id, instruction[:80],
        )

        if conversation_id:
            state = self._active_conversations.get(conversation_id)
            if not state:
                logger.warning("Conversation %s not found in active conversations", conversation_id)
                return f"Conversation {conversation_id} is not active."
            participants = await self._conversation_dao.get_participants(conversation_id)
            if not any(p["user_id"] == user_id for p in participants):
                logger.warning("User %s is not a participant in conversation %s", user_id, conversation_id)
                return "You are not a participant in that conversation."
            return await self._store_instruction(conversation_id, user_id, instruction, state)

        # Search all active conversations for this user
        for conv_id, state in self._active_conversations.items():
            participants = await self._conversation_dao.get_participants(conv_id)
            if any(p["user_id"] == user_id for p in participants):
                logger.info("Found active conversation %s for user %s", conv_id, user_id)
                return await self._store_instruction(conv_id, user_id, instruction, state)

        logger.warning("No active conversation found for user %s", user_id)
        return None

    async def _store_instruction(
        self,
        conversation_id: str,
        user_id: str,
        instruction: str,
        state: ConversationState,
    ) -> str:
        """Store a private instruction and mark the agent as ready.

        If the conversation hasn't started yet and all agents now have
        instructions, the conversation starts immediately (cancelling
        the pending timeout).
        """
        await self._conversation_dao.add_conversation_message(
            conversation_id=conversation_id,
            participant_user_id=user_id,
            content=instruction,
            iteration_number=state.current_iteration,
            is_private_instruction=True,
        )
        # Mark the agent as having received instructions
        state.awaiting_instructions.discard(user_id)

        # If this user has a pending mid-conversation clarification, signal it
        if user_id in state.awaiting_clarification:
            state.pending_clarifications[user_id] = instruction
            event = state.clarification_events.get(user_id)
            if event:
                event.set()

        participant = await self._get_participant(conversation_id, user_id)
        agent_name = participant.get("agent_name") or user_id if participant else user_id

        # Notify the creator that this agent is now ready
        await self._notify_creator(
            state,
            f"Agent '{agent_name}' received instructions from owner.",
        )

        # If the conversation hasn't started yet and all agents are ready,
        # start immediately (cancel the timeout).
        if not state.started and not state.awaiting_instructions:
            self._cancel_pending_start(conversation_id)
            await self._begin_conversation(conversation_id)

        return (
            f"Instructions saved for {agent_name}. "
            f"{'Conversation is starting now!' if state.started else 'Waiting for other agents...'}"
        )

    async def cancel_conversation(self, conversation_id: str) -> bool:
        """Cancel an active conversation and deliver transcript to all participants."""
        state = self._active_conversations.get(conversation_id)
        if not state:
            return False

        await self._end_conversation(
            conversation_id=conversation_id,
            status=ConversationStatus.CANCELLED,
            exit_reason="Cancelled by user",
        )
        return True

    async def get_conversation_transcript(self, conversation_id: str) -> str:
        """Generate a transcript for a conversation (active or ended)."""
        conv_data = await self._conversation_dao.get_conversation_by_id(conversation_id)
        if not conv_data:
            return f"Conversation {conversation_id} not found."

        messages = await self._conversation_dao.get_conversation_messages(conversation_id)
        participants = await self._conversation_dao.get_participants(conversation_id)

        return self._format_transcript(conv_data, participants, messages)

    async def get_active_conversation_for_user(self, user_id: str) -> dict | None:
        """Get active conversation where user is participating or is creator."""
        for conv_id, state in self._active_conversations.items():
            if state.creator_user_id == user_id:
                return {"conversation_id": conv_id, "state": state}
            participants = await self._conversation_dao.get_participants(conv_id)
            if any(p["user_id"] == user_id for p in participants):
                return {"conversation_id": conv_id, "state": state}
        return None

    def is_conversation_active(self, conversation_id: str) -> bool:
        return conversation_id in self._active_conversations

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _extract_and_analyze_parameters(
        self,
        conversation_id: str,
        participants: list[dict],
    ) -> None:
        """Extract decision parameters from all agents' instructions using LLM.

        Populates ``state.parameter_analysis`` on success.  On failure the
        field remains ``None`` and the conversation falls back to keyword-based
        consensus.
        """
        state = self._active_conversations.get(conversation_id)
        if not state or not self._manager_agent:
            return

        messages = await self._conversation_dao.get_conversation_messages(conversation_id)

        # Collect instructions per agent
        agent_instructions: dict[str, str] = {}
        agent_names: dict[str, str] = {}
        for p in participants:
            uid = p["user_id"]
            agent_names[uid] = p.get("agent_name") or uid
            instructions = [
                msg["content"]
                for msg in messages
                if msg.get("is_private_instruction") and msg["participant_user_id"] == uid
            ]
            if instructions:
                agent_instructions[uid] = " | ".join(instructions)

        if len(agent_instructions) < 2:
            logger.info(
                "Conversation %s: fewer than 2 agents with instructions, skipping parameter extraction",
                conversation_id,
            )
            return

        try:
            prompt = self._manager_agent.build_parameter_extraction_prompt(
                topic=state.topic,
                agent_instructions=agent_instructions,
                agent_names=agent_names,
            )
            raw_response = await self._agent_service.process_message(
                user_id="__conversation_manager__",
                message=prompt,
            )
            state.parameter_analysis = self._manager_agent.parse_parameter_analysis(raw_response)

            if state.parameter_analysis:
                logger.info(
                    "Conversation %s: extracted %d parameters, all_aligned=%s",
                    conversation_id,
                    len(state.parameter_analysis.parameters),
                    state.parameter_analysis.all_aligned,
                )
            else:
                logger.warning(
                    "Conversation %s: parameter extraction returned unparseable response",
                    conversation_id,
                )
        except Exception:
            logger.exception("Failed to extract parameters for conversation %s", conversation_id)

    async def _send_to_user(self, user_id: str, text: str, state: ConversationState) -> None:
        """Send a message to a user, resolving their chat_id if needed."""
        chat_id = state.known_chat_ids.get(user_id)
        if chat_id is None:
            chat_id = await self._resolve_chat_id(user_id)
            if chat_id is not None:
                state.known_chat_ids[user_id] = chat_id

        if chat_id is None:
            logger.warning("Cannot resolve chat_id for user %s", user_id)
            return

        try:
            await self._send_message(chat_id, text)
        except Exception:
            logger.exception("Failed to send message to user %s", user_id)

    async def _notify_creator(self, state: ConversationState, text: str) -> None:
        await self._send_to_user(state.creator_user_id, text, state)

    async def _notify_all_participants(self, state: ConversationState, text: str) -> None:
        """Send a message to every participant + creator."""
        participants = await self._conversation_dao.get_participants(state.conversation_id)
        notified: set[str] = set()

        await self._send_to_user(state.creator_user_id, text, state)
        notified.add(state.creator_user_id)

        for p in participants:
            uid = p["user_id"]
            if uid not in notified:
                await self._send_to_user(uid, text, state)
                notified.add(uid)

    def _schedule_start(self, conversation_id: str, timeout_seconds: int) -> None:
        """Schedule the conversation to start after a timeout.

        If all agents provide instructions before the timeout, the
        conversation starts early (see ``_store_instruction``).
        """
        async def _wait_and_start() -> None:
            await asyncio.sleep(timeout_seconds)
            state = self._active_conversations.get(conversation_id)
            if state and not state.started:
                await self._begin_conversation(conversation_id)

        task = asyncio.create_task(_wait_and_start())
        state = self._active_conversations.get(conversation_id)
        if state:
            state.start_timer_task = task

    def _cancel_pending_start(self, conversation_id: str) -> None:
        """Cancel the pending timeout for a conversation."""
        state = self._active_conversations.get(conversation_id)
        if state and state.start_timer_task and not state.start_timer_task.done():
            state.start_timer_task.cancel()
            state.start_timer_task = None

    async def _begin_conversation(self, conversation_id: str) -> None:
        """Start the round-robin conversation.

        Only agents whose owners provided instructions participate.
        Agents still awaiting instructions are excluded and marked
        as agreed (so they don't block consensus).
        """
        state = self._active_conversations.get(conversation_id)
        if not state or state.started:
            logger.info("_begin_conversation(%s): already started or not found", conversation_id)
            return

        state.started = True
        logger.info(
            "Beginning conversation %s: awaiting_instructions=%s",
            conversation_id, state.awaiting_instructions,
        )

        # Exclude agents that never provided instructions
        excluded_uids = list(state.awaiting_instructions)
        for uid in excluded_uids:
            participant = await self._get_participant(conversation_id, uid)
            display = (participant.get("agent_name") or uid) if participant else uid
            await self._notify_creator(
                state,
                f"Agent '{display}' did not provide instructions in time — excluded.",
            )
            await self._send_to_user(
                uid,
                f"The conversation '{state.topic}' has started without your agent "
                f"because you didn't provide instructions in time.",
                state,
            )
            # Mark as agreed so they don't block consensus checks
            await self._conversation_dao.mark_participant_agreed(conversation_id, uid)
        state.awaiting_instructions.clear()

        # Check if any participants actually have instructions
        participants = await self._conversation_dao.get_pending_participants(conversation_id)
        if not participants:
            await self._notify_creator(
                state,
                "No agents provided instructions. Conversation cancelled.",
            )
            await self._end_conversation(
                conversation_id=conversation_id,
                status=ConversationStatus.CANCELLED,
                exit_reason="No agents provided instructions before the timeout.",
            )
            return

        participant_names = [p.get("agent_name") or p["user_id"] for p in participants]

        # Extract decision parameters from all agents' instructions
        await self._extract_and_analyze_parameters(conversation_id, participants)

        start_msg = (
            f"Conversation started!\n\n"
            f"Topic: {state.topic}\n"
            f"Participating agents: {', '.join(participant_names)}\n"
            f"Max iterations: {state.max_iterations}\n"
            f"Use 'conversation status {conversation_id}' for a live transcript."
        )

        if state.parameter_analysis and not state.parameter_analysis.all_aligned:
            start_msg += (
                f"\n\nParameter analysis:\n{state.parameter_analysis.summary}\n"
                f"Unresolved: "
                + ", ".join(
                    p.name for p in state.parameter_analysis.parameters if not p.is_aligned
                )
            )

        await self._notify_all_participants(state, start_msg)

        await self._run_round(conversation_id)

    async def _run_round(self, conversation_id: str) -> None:
        """Run one full round of the conversation.

        Iterates through all pending (non-agreed) participants,
        invoking each agent in turn and broadcasting the response.
        """
        state = self._active_conversations.get(conversation_id)
        if not state:
            return

        # Increment iteration (one per round)
        await self._conversation_dao.increment_conversation_iteration(conversation_id)
        conv_data = await self._conversation_dao.get_conversation_by_id(conversation_id)
        state.current_iteration = conv_data["current_iteration"] if conv_data else state.current_iteration + 1

        if state.current_iteration > state.max_iterations:
            await self._end_conversation(
                conversation_id=conversation_id,
                status=ConversationStatus.MAX_ITERATIONS_REACHED,
                exit_reason=f"Max iterations ({state.max_iterations}) reached",
            )
            return

        participants = await self._conversation_dao.get_pending_participants(conversation_id)

        if not participants:
            if await self._conversation_dao.check_all_agreed(conversation_id):
                await self._end_conversation(
                    conversation_id=conversation_id,
                    status=ConversationStatus.CONSENSUS_REACHED,
                    exit_reason="All participants agreed",
                )
            else:
                await self._end_conversation(
                    conversation_id=conversation_id,
                    status=ConversationStatus.MAX_ITERATIONS_REACHED,
                    exit_reason=f"Max iterations ({state.max_iterations}) reached",
                )
            return

        await self._notify_all_participants(
            state, f"--- Round {state.current_iteration}/{state.max_iterations} ---"
        )

        # Process each participant's turn sequentially
        for participant in participants:
            # Re-check: conversation may have ended mid-round
            if conversation_id not in self._active_conversations:
                return

            user_id = participant["user_id"]

            # Skip if participant agreed during this round
            p_data = await self._get_participant(conversation_id, user_id)
            if p_data and p_data.get("has_agreed"):
                continue

            await self._execute_agent_turn(conversation_id, participant)

            # Check if consensus was reached after this turn
            if conversation_id not in self._active_conversations:
                return

        # All participants processed — check if we should continue
        if conversation_id not in self._active_conversations:
            return

        # Parameter-based consensus check (when parameter analysis is active)
        if state.parameter_analysis and state.parameter_analysis.parameters and self._manager_agent:
            aligned = self._manager_agent.check_parameter_alignment(state.parameter_analysis)

            if not aligned:
                # Try LLM-based semantic equivalence check as fallback
                aligned = await self._check_parameter_alignment_llm(conversation_id)

            if aligned:
                # All parameters aligned — mark everyone as agreed
                all_participants = await self._conversation_dao.get_participants(conversation_id)
                for p in all_participants:
                    await self._conversation_dao.mark_participant_agreed(
                        conversation_id, p["user_id"],
                    )

                aligned_summary = ", ".join(
                    f"{p.name}={p.aligned_value}"
                    for p in state.parameter_analysis.parameters
                )
                await self._end_conversation(
                    conversation_id=conversation_id,
                    status=ConversationStatus.CONSENSUS_REACHED,
                    exit_reason=f"All parameters aligned: {aligned_summary}",
                )
                return

            # Not aligned — broadcast status and continue
            unresolved = [
                p for p in state.parameter_analysis.parameters if not p.is_aligned
            ]
            await self._notify_all_participants(
                state,
                f"After round {state.current_iteration}: "
                f"{len(unresolved)} parameter(s) still unresolved: "
                f"{', '.join(p.name for p in unresolved)}",
            )
        else:
            # Fallback: keyword-based consensus
            if await self._conversation_dao.check_all_agreed(conversation_id):
                await self._end_conversation(
                    conversation_id=conversation_id,
                    status=ConversationStatus.CONSENSUS_REACHED,
                    exit_reason="All participants agreed",
                )
                return

        if state.current_iteration >= state.max_iterations:
            await self._end_conversation(
                conversation_id=conversation_id,
                status=ConversationStatus.MAX_ITERATIONS_REACHED,
                exit_reason=f"Max iterations ({state.max_iterations}) reached",
            )
        else:
            await self._run_round(conversation_id)

    async def _execute_agent_turn(
        self,
        conversation_id: str,
        participant: dict,
    ) -> None:
        """Invoke the agent for a participant and broadcast their response.

        Builds conversation context, calls ``agent_service.process_message()``,
        stores the response, broadcasts it, and checks for agreement.
        """
        state = self._active_conversations.get(conversation_id)
        if not state:
            return

        user_id = participant["user_id"]
        agent_name = participant.get("agent_name") or user_id

        state.current_participant_user_id = user_id
        state.waiting_for_agent_id = user_id

        logger.info(
            "Conversation %s: invoking agent for %s (round %d/%d)",
            conversation_id, agent_name, state.current_iteration, state.max_iterations,
        )

        # Build the prompt with full conversation context
        context_prompt = await self._build_agent_context(conversation_id, participant)

        try:
            response = await self._agent_service.process_message(
                user_id=user_id,
                message=context_prompt,
            )
        except Exception:
            logger.exception(
                "Conversation %s: agent for %s failed to respond",
                conversation_id, agent_name,
            )
            response = f"[Agent {agent_name} failed to respond this round]"

        state.waiting_for_agent_id = None
        state.current_participant_user_id = None

        logger.info(
            "Conversation %s: agent %s responded (%d chars)",
            conversation_id, agent_name, len(response),
        )

        # Store the message
        await self._conversation_dao.add_conversation_message(
            conversation_id=conversation_id,
            participant_user_id=user_id,
            content=response,
            iteration_number=state.current_iteration,
            is_private_instruction=False,
        )

        # Broadcast to all participants
        await self._notify_all_participants(
            state, f"[{agent_name}]: {response}"
        )

        # Parse structured parameter response and handle mid-conv DM
        if state.parameter_analysis and self._manager_agent:
            agent_params = self._manager_agent.parse_agent_parameter_response(response)
            if agent_params:
                self._update_agent_positions(state, user_id, agent_name, agent_params)

                # Check for need_owner_input
                needs_input = [
                    p for p in agent_params
                    if p.get("status") == "need_owner_input"
                ]
                if needs_input:
                    clarified = await self._request_owner_clarification(
                        conversation_id, user_id, agent_name, needs_input,
                    )
                    if clarified:
                        # Re-invoke agent with updated context
                        context_prompt = await self._build_agent_context(
                            conversation_id, participant,
                        )
                        try:
                            followup = await self._agent_service.process_message(
                                user_id=user_id,
                                message=context_prompt,
                            )
                            await self._conversation_dao.add_conversation_message(
                                conversation_id=conversation_id,
                                participant_user_id=user_id,
                                content=followup,
                                iteration_number=state.current_iteration,
                            )
                            await self._notify_all_participants(
                                state,
                                f"[{agent_name} (after owner clarification)]: {followup}",
                            )
                            # Re-parse updated positions
                            followup_params = self._manager_agent.parse_agent_parameter_response(
                                followup,
                            )
                            if followup_params:
                                self._update_agent_positions(
                                    state, user_id, agent_name, followup_params,
                                )
                            response = followup
                        except Exception:
                            logger.exception(
                                "Conversation %s: followup from %s failed",
                                conversation_id, agent_name,
                            )

        # Check for agreement (keyword-based only when no parameter analysis)
        if not (state.parameter_analysis and state.parameter_analysis.parameters):
            await self._check_keyword_agreement(conversation_id, user_id, response)

    @staticmethod
    def _update_agent_positions(
        state: ConversationState,
        user_id: str,
        agent_name: str,
        agent_params: list[dict],
    ) -> None:
        """Update parameter_analysis with an agent's structured response."""
        if not state.parameter_analysis:
            return

        for ap in agent_params:
            param_name = ap.get("name", "")
            my_position = ap.get("my_position", "")
            status = ap.get("status", "proposing")

            for param in state.parameter_analysis.parameters:
                if param.name.lower() == param_name.lower():
                    # Update this agent's position
                    for pos in param.positions:
                        if pos.agent_user_id == user_id:
                            pos.position = my_position
                            pos.source = "conversation"
                            break
                    else:
                        # Agent wasn't in positions yet — add them
                        from app.models.domain import ParameterPosition

                        param.positions.append(
                            ParameterPosition(
                                agent_user_id=user_id,
                                agent_name=agent_name,
                                position=my_position,
                                source="conversation",
                            )
                        )
                    break

    async def _request_owner_clarification(
        self,
        conversation_id: str,
        user_id: str,
        agent_name: str,
        parameters_needing_input: list[dict],
        timeout: int = 300,
    ) -> bool:
        """DM the agent's owner asking for clarification on specific parameters.

        Args:
            conversation_id: The conversation ID.
            user_id: The agent's owner user_id.
            agent_name: Display name of the agent.
            parameters_needing_input: List of parameter dicts with status=need_owner_input.
            timeout: Max seconds to wait (default 5 minutes).

        Returns:
            True if owner responded within timeout.
        """
        state = self._active_conversations.get(conversation_id)
        if not state:
            return False

        param_names = [p.get("name", "unknown") for p in parameters_needing_input]

        # Set up the clarification event
        event = asyncio.Event()
        state.clarification_events[user_id] = event
        state.pending_clarifications[user_id] = None
        state.awaiting_clarification.add(user_id)

        # Build the DM message
        param_details = "\n".join(
            f"  - {p.get('name', '?')}: current position = {p.get('my_position', '?')}"
            for p in parameters_needing_input
        )
        timeout_label = f"{timeout // 60} minute{'s' if timeout // 60 != 1 else ''}"

        await self._send_to_user(
            user_id,
            f"Your agent '{agent_name}' needs your input on:\n\n"
            f"{param_details}\n\n"
            f"You have {timeout_label} to respond.\n"
            f"Use: conversation instruct <your clarification>\n\n"
            f"If you don't respond, your agent will proceed with its best judgment.",
            state,
        )

        await self._notify_all_participants(
            state,
            f"Waiting for {agent_name}'s owner to clarify: {', '.join(param_names)} "
            f"(up to {timeout_label})...",
        )

        # Wait for the event or timeout
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            owner_responded = True
        except asyncio.TimeoutError:
            owner_responded = False

        # Clean up
        state.awaiting_clarification.discard(user_id)
        state.clarification_events.pop(user_id, None)
        state.pending_clarifications.pop(user_id, None)

        if owner_responded:
            await self._notify_all_participants(
                state, f"{agent_name}'s owner provided clarification. Continuing...",
            )
        else:
            await self._notify_all_participants(
                state,
                f"{agent_name}'s owner did not respond in time. "
                f"Agent will proceed with available information.",
            )

        return owner_responded

    async def _check_parameter_alignment_llm(self, conversation_id: str) -> bool:
        """Use LLM to check semantic equivalence of near-matching positions.

        Called as fallback when deterministic check doesn't find exact matches.

        Returns:
            True if the LLM determines all parameters are aligned.
        """
        state = self._active_conversations.get(conversation_id)
        if not state or not state.parameter_analysis or not self._manager_agent:
            return False

        messages = await self._conversation_dao.get_conversation_messages(conversation_id)
        conversation_history = "\n".join(
            f"{msg['participant_user_id']}: {msg['content']}"
            for msg in messages
            if not msg.get("is_private_instruction")
        )

        prompt = self._manager_agent.build_alignment_check_prompt(
            topic=state.topic,
            current_analysis=state.parameter_analysis,
            conversation_history=conversation_history,
        )

        try:
            raw_response = await self._agent_service.process_message(
                user_id="__conversation_manager__",
                message=prompt,
            )
            updated = self._manager_agent.parse_parameter_analysis(raw_response)
            if updated:
                state.parameter_analysis = updated
                state.parameter_analysis.last_updated_iteration = state.current_iteration
                return updated.all_aligned
        except Exception:
            logger.exception(
                "Failed LLM alignment check for conversation %s", conversation_id,
            )

        return False

    async def _build_agent_context(
        self,
        conversation_id: str,
        participant: dict,
    ) -> str:
        """Build conversation context for an agent, including owner instructions."""
        state = self._active_conversations.get(conversation_id)
        if not state:
            return "Error: Conversation not found"

        messages = await self._conversation_dao.get_conversation_messages(conversation_id)

        # Collect private instructions from this participant's owner
        user_id = participant["user_id"]
        owner_instructions = [
            msg["content"]
            for msg in messages
            if msg.get("is_private_instruction") and msg["participant_user_id"] == user_id
        ]

        lines = [
            "You are participating in a multi-agent conversation.",
            "",
            f"Topic: {state.topic}",
            f"Current round: {state.current_iteration or 1}/{state.max_iterations}",
            "",
            f"Your role: You are '{participant.get('agent_name') or user_id}', "
            f"an AI agent working with other agents to reach consensus.",
        ]

        if state.agent_instructions:
            lines.append(f"\nGeneral instructions: {state.agent_instructions}")

        if owner_instructions:
            lines.append("\nInstructions from your owner:")
            for idx, instr in enumerate(owner_instructions, 1):
                lines.append(f"  {idx}. {instr}")
        else:
            lines.append("\nNo specific instructions from your owner.")

        # Include parameter structure when available
        has_unresolved = (
            state.parameter_analysis
            and state.parameter_analysis.parameters
            and not state.parameter_analysis.all_aligned
        )

        if has_unresolved:
            lines.append("")
            lines.append("=" * 50)
            lines.append("DECISION PARAMETERS")
            lines.append("=" * 50)

            for param in state.parameter_analysis.parameters:
                status = "ALIGNED" if param.is_aligned else "CONFLICT"
                lines.append(f"\n[{status}] {param.name}: {param.description}")
                for pos in param.positions:
                    marker = "  <-- YOUR POSITION" if pos.agent_user_id == user_id else ""
                    lines.append(f"  - {pos.agent_name or pos.agent_user_id}: {pos.position}{marker}")
                if param.is_aligned:
                    lines.append(f"  Agreed value: {param.aligned_value}")

            unresolved = [p for p in state.parameter_analysis.parameters if not p.is_aligned]
            if unresolved:
                lines.append(f"\nUNRESOLVED: {', '.join(p.name for p in unresolved)}")

            lines.append("=" * 50)

        lines.append("")
        lines.append("CONVERSATION HISTORY:")

        for msg in messages:
            if msg.get("is_private_instruction"):
                continue  # Don't show other agents' private instructions
            sender_name = msg.get("participant_user_id", "Unknown")
            lines.append(f"{sender_name}: {msg['content']}")

        lines.append("")

        if has_unresolved:
            lines.append(
                "IMPORTANT: There are unresolved parameter conflicts. "
                "Focus on resolving these specific points. "
                "Do NOT say 'I agree' generically. "
                "Use the parameter structure below to indicate your position on each point.\n"
                "\nIf you need your owner's input on a parameter, set its status to "
                "'need_owner_input'.\n"
                "\nYou MUST include a [PARAMETERS] block at the end of your response:\n"
                "\n[PARAMETERS]\n"
                '{\n'
                '  "parameters": [\n'
                '    {"name": "<param_name>", "my_position": "<your proposed value>", '
                '"status": "proposing|accepted|need_owner_input"}\n'
                "  ]\n"
                "}\n"
                "[/PARAMETERS]\n"
                "\nStatus values:\n"
                '- "proposing": you suggest this value\n'
                '- "accepted": you accept the other agent\'s proposed value\n'
                '- "need_owner_input": you need to ask your owner before deciding'
            )
        else:
            lines.append(
                "Please respond to the conversation. "
                "If you agree with the current direction, clearly express your agreement."
            )

        return "\n".join(lines)

    async def _check_keyword_agreement(
        self,
        conversation_id: str,
        user_id: str,
        content: str,
    ) -> None:
        """Keyword-based agreement detection (fallback when no parameter analysis).

        If all participants agree, ends the conversation immediately.
        Otherwise, the round loop in ``_run_round`` continues normally.
        """
        agreed_keywords = ["i agree", "agree", "sounds good", "works for me", "yes, let's do it"]
        content_lower = content.lower()
        has_agreement = any(keyword in content_lower for keyword in agreed_keywords)

        if has_agreement:
            await self._conversation_dao.mark_participant_agreed(conversation_id, user_id)

            state = self._active_conversations.get(conversation_id)
            if state:
                participant = await self._get_participant(conversation_id, user_id)
                agent_name = participant.get("agent_name") or user_id if participant else user_id
                await self._notify_all_participants(
                    state, f"Agent '{agent_name}' has indicated agreement."
                )

            if await self._conversation_dao.check_all_agreed(conversation_id):
                await self._end_conversation(
                    conversation_id=conversation_id,
                    status=ConversationStatus.CONSENSUS_REACHED,
                    exit_reason="All participants agreed",
                )

    async def _get_participant(self, conversation_id: str, user_id: str) -> dict | None:
        participants = await self._conversation_dao.get_participants(conversation_id)
        for p in participants:
            if p["user_id"] == user_id:
                return p
        return None

    async def _end_conversation(
        self,
        conversation_id: str,
        status: ConversationStatus,
        exit_reason: str,
    ) -> None:
        """End a conversation and deliver the transcript to all participants."""
        state = self._active_conversations.get(conversation_id)
        if not state:
            logger.warning("Conversation %s not found in state", conversation_id)
            return

        await self._conversation_dao.update_conversation_status(
            conversation_id=conversation_id,
            status=status.value,
            exit_reason=exit_reason,
        )

        transcript = await self.get_conversation_transcript(conversation_id)

        status_label = {
            ConversationStatus.CONSENSUS_REACHED: "Consensus reached",
            ConversationStatus.MAX_ITERATIONS_REACHED: "Max iterations reached",
            ConversationStatus.CANCELLED: "Cancelled",
        }.get(status, "Ended")

        final_message = (
            f"Conversation ended: {status_label}\n"
            f"Reason: {exit_reason}\n\n"
            f"--- TRANSCRIPT ---\n{transcript}"
        )

        await self._notify_all_participants(state, final_message)

        del self._active_conversations[conversation_id]

    @staticmethod
    def _format_transcript(
        conv_data: dict,
        participants: list[dict],
        messages: list[dict],
    ) -> str:
        """Format a conversation into a readable transcript."""
        participant_names = {
            p["user_id"]: p.get("agent_name") or p["user_id"]
            for p in participants
        }

        lines = [
            f"Topic: {conv_data['topic']}",
            f"Status: {conv_data['status']}",
            f"Iterations: {conv_data['current_iteration']}/{conv_data['max_iterations']}",
            f"Created: {conv_data.get('created_at', 'N/A')}",
        ]

        if conv_data.get("exit_reason"):
            lines.append(f"Exit reason: {conv_data['exit_reason']}")

        lines.append("\nParticipants:")
        for p in participants:
            name = p.get("agent_name") or p["user_id"]
            agreed = " (agreed)" if p.get("has_agreed") else ""
            lines.append(f"  - {name}{agreed}")

        lines.append("\nMessages:")
        current_iteration = None
        for msg in messages:
            if msg.get("is_private_instruction"):
                continue
            iteration = msg.get("iteration_number", 0)
            if iteration != current_iteration:
                current_iteration = iteration
                lines.append(f"\n  -- Round {iteration} --")
            sender = participant_names.get(msg["participant_user_id"], msg["participant_user_id"])
            lines.append(f"  [{sender}]: {msg['content']}")

        return "\n".join(lines)


class ConversationState:
    """In-memory state for an active conversation."""

    def __init__(
        self,
        conversation_id: str,
        topic: str,
        max_iterations: int,
        creator_user_id: str,
        agent_instructions: str | None = None,
        instruction_timeout: int = DEFAULT_INSTRUCTION_TIMEOUT,
    ) -> None:
        self.conversation_id = conversation_id
        self.topic = topic
        self.max_iterations = max_iterations
        self.creator_user_id = creator_user_id
        self.agent_instructions = agent_instructions
        self.instruction_timeout = instruction_timeout

        self.current_iteration = 0
        self.current_participant_user_id: str | None = None
        self.waiting_for_agent_id: str | None = None
        # Maps user_id -> Telegram chat_id for DM delivery
        self.known_chat_ids: dict[str, int] = {}
        # Agents whose owners haven't sent instructions yet.
        self.awaiting_instructions: set[str] = set()
        # Whether the conversation has started (rounds are running).
        self.started: bool = False
        # Reference to the asyncio task that waits for the timeout.
        self.start_timer_task: asyncio.Task | None = None

        # Parameter-based consensus tracking
        self.parameter_analysis: ParameterAnalysis | None = None
        # Mid-conversation clarification: user_id -> asyncio.Event
        self.clarification_events: dict[str, asyncio.Event] = {}
        # Mid-conversation clarification: user_id -> latest clarification text
        self.pending_clarifications: dict[str, str | None] = {}
        # User IDs whose agents have requested owner clarification
        self.awaiting_clarification: set[str] = set()
