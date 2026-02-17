"""Conversation Manager Agent for multi-agent conversations.

This is a specialized agent that evaluates agent responses
to detect agreement and determine when conversations should end.

Unlike regular agents, this agent has access to the conversation
DAO and can directly update participant agreement status.

It also handles parameter extraction from agent instructions and
structured parameter-based consensus tracking.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from app.models.domain import ConversationParameter, ParameterAnalysis, ParameterPosition

if TYPE_CHECKING:
    from app.dao.conversation_dao import ConversationDAO

logger = logging.getLogger(__name__)


class ConversationManagerAgent:
    """Agent that manages multi-agent conversations.

    Evaluates agent responses for agreement signals, tracks iteration
    count, and determines when conversations should end based on
    consensus or max iterations.
    """

    def __init__(
        self,
        conversation_dao: ConversationDAO,
    ) -> None:
        """Initialize the conversation manager agent.

        Args:
            conversation_dao: DAO for accessing conversation data.
        """
        self._conversation_dao = conversation_dao

    async def evaluate_response(
        self,
        conversation_id: str,
        agent_user_id: str,
        response_content: str,
    ) -> dict[str, Any]:
        """Evaluate an agent's response for agreement.

        Args:
            conversation_id: The conversation ID.
            agent_user_id: The user ID of the responding agent.
            response_content: The agent's response content.

        Returns:
            Dictionary with evaluation results:
            - "has_agreed": bool - whether agent agreed
            - "should_continue": bool - whether conversation should continue
            - "reason": str - explanation for the decision
        """
        # Get conversation context
        conv_data = await self._conversation_dao.get_conversation_by_id(conversation_id)
        if not conv_data:
            return {
                "has_agreed": False,
                "should_continue": True,
                "reason": "Conversation not found",
            }

        topic = conv_data.get("topic", "")
        max_iterations = conv_data.get("max_iterations", 5)
        current_iteration = conv_data.get("current_iteration", 1)

        # Build evaluation prompt
        prompt = f"""You are evaluating an agent's response in a multi-agent conversation.

Topic: {topic}
Current iteration: {current_iteration}/{max_iterations}
Agent's response: {response_content[:500]}

Determine if the agent has agreed to the topic or should continue the discussion.
Consider:
- Does the agent explicitly agree?
- Does the agent indicate consensus without objection?
- Is the agent asking for more information (continuing)?
- Has the agent clearly stated their position?

Return JSON with format:
{{
    "has_agreed": true/false,
    "should_continue": true/false,
    "reason": "brief explanation"
}}"""

        # For now, do simple keyword detection
        # In production, this would call the agent with this prompt
        response = await self._simple_agreement_detection(response_content, topic)

        return response

    async def _simple_agreement_detection(
        self,
        response: str,
        topic: str,
    ) -> dict[str, Any]:
        """Simple keyword-based agreement detection.

        Args:
            response: The agent's response.
            topic: The conversation topic.

        Returns:
            Dictionary with evaluation results.
        """
        response_lower = response.lower().strip()

        # Check for explicit disagreement
        disagreement_keywords = ["disagree", "don't", "won't work", "no good", "not feasible"]
        has_disagreement = any(kw in response_lower for kw in disagreement_keywords)

        if has_disagreement:
            return {
                "has_agreed": False,
                "should_continue": True,
                "reason": "Agent expressed disagreement",
            }

        # Check for explicit agreement
        agreement_keywords = [
            "i agree",
            "agree",
            "sounds good",
            "works for me",
            "let's do it",
            "yes, let's proceed",
            "confirmed",
            "approved",
        ]
        has_agreement = any(kw in response_lower for kw in agreement_keywords)

        # Check for continuing/need more info patterns
        continuing_patterns = [
            "but",
            "however",
            "although",
            "on the condition that",
            "assuming",
            "what about",
            "how about",
            "would need",
        ]
        is_continuing = any(pattern in response_lower for pattern in continuing_patterns)

        # Check for question patterns (agent asking questions, not agreeing)
        question_patterns = [
            "what if",
            "how will",
            "what happens when",
            "what do you think",
            "should we consider",
            "i'm concerned about",
            "not sure about",
        ]
        is_questioning = any(pattern in response_lower for pattern in question_patterns)

        # Determine agreement
        if has_agreement and not is_continuing and not is_questioning:
            return {
                "has_agreed": True,
                "should_continue": True,
                "reason": "Agent explicitly agreed",
            }

        # Very short responses that don't clearly indicate either way
        if len(response) < 30:
            return {
                "has_agreed": False,
                "should_continue": True,
                "reason": "Response too short to determine agreement",
            }

        # Default: not agreed, continue
        return {
            "has_agreed": False,
            "should_continue": True,
            "reason": "No clear agreement detected, continuing discussion",
        }

    # ------------------------------------------------------------------
    # Parameter extraction & alignment
    # ------------------------------------------------------------------

    def build_parameter_extraction_prompt(
        self,
        topic: str,
        agent_instructions: dict[str, str],
        agent_names: dict[str, str],
    ) -> str:
        """Build the LLM prompt for extracting decision parameters.

        Args:
            topic: The conversation topic.
            agent_instructions: Map of user_id -> instruction text.
            agent_names: Map of user_id -> display name.

        Returns:
            Prompt string for the LLM.
        """
        instructions_section = "\n".join(
            f"- {agent_names.get(uid, uid)} ({uid}): {instr}"
            for uid, instr in agent_instructions.items()
        )

        return f"""You are analyzing instructions for a multi-agent conversation to extract decision parameters.

Topic: {topic}

Agent instructions:
{instructions_section}

Extract the KEY DECISION PARAMETERS from these instructions. A parameter is any point
where agents need to agree on a specific value (time, location, food, approach, etc.).

For each parameter, identify each agent's position based on their instructions.

Return ONLY valid JSON (no markdown fences, no extra text) in this format:
{{
  "parameters": [
    {{
      "name": "short_parameter_name",
      "description": "What this parameter is about",
      "positions": [
        {{"agent_user_id": "user_id_here", "agent_name": "name_here", "position": "their stated preference"}},
        {{"agent_user_id": "user_id_here", "agent_name": "name_here", "position": "their stated preference"}}
      ],
      "is_aligned": false,
      "aligned_value": null
    }}
  ],
  "summary": "One sentence summary of conflicts and agreements",
  "all_aligned": false
}}

Rules:
- If agents agree on a parameter, set is_aligned=true and aligned_value to the agreed value.
- If agents disagree, set is_aligned=false and aligned_value=null.
- If an agent doesn't mention a parameter, set their position to "no preference stated".
- Set all_aligned=true only if EVERY parameter has is_aligned=true."""

    def parse_parameter_analysis(self, raw_response: str) -> ParameterAnalysis | None:
        """Parse an LLM response into a ParameterAnalysis.

        Handles JSON in code fences, with preamble text, etc.

        Args:
            raw_response: Raw LLM response text.

        Returns:
            Parsed ParameterAnalysis or None if parsing fails.
        """
        json_str = self._extract_json(raw_response)
        if not json_str:
            logger.warning("Could not extract JSON from parameter extraction response")
            return None

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("Failed to parse parameter extraction JSON: %s", json_str[:200])
            return None

        try:
            parameters = []
            for p in data.get("parameters", []):
                positions = [
                    ParameterPosition(
                        agent_user_id=pos["agent_user_id"],
                        agent_name=pos.get("agent_name"),
                        position=pos["position"],
                        source=pos.get("source", "initial_instruction"),
                    )
                    for pos in p.get("positions", [])
                ]
                parameters.append(
                    ConversationParameter(
                        name=p["name"],
                        description=p.get("description", ""),
                        positions=positions,
                        is_aligned=p.get("is_aligned", False),
                        aligned_value=p.get("aligned_value"),
                    )
                )

            return ParameterAnalysis(
                parameters=parameters,
                summary=data.get("summary", ""),
                all_aligned=data.get("all_aligned", False),
                last_updated_iteration=0,
            )
        except (KeyError, TypeError) as exc:
            logger.warning("Failed to build ParameterAnalysis from data: %s", exc)
            return None

    def parse_agent_parameter_response(
        self, response_text: str,
    ) -> list[dict] | None:
        """Extract the [PARAMETERS]...[/PARAMETERS] block from an agent response.

        Args:
            response_text: The full agent response.

        Returns:
            List of parameter dicts or None if no block found.
        """
        match = re.search(
            r"\[PARAMETERS\]\s*(.*?)\s*\[/PARAMETERS\]",
            response_text,
            re.DOTALL,
        )
        if not match:
            return None

        json_str = self._extract_json(match.group(1))
        if not json_str:
            return None

        try:
            data = json.loads(json_str)
            return data.get("parameters", data) if isinstance(data, dict) else data
        except json.JSONDecodeError:
            logger.warning("Failed to parse agent parameter block")
            return None

    def check_parameter_alignment(
        self, analysis: ParameterAnalysis,
    ) -> bool:
        """Deterministic check: are all parameters aligned?

        A parameter is aligned when all agents' latest positions match
        (case-insensitive) or all agents have accepted the same value.

        Args:
            analysis: Current parameter analysis.

        Returns:
            True if all parameters are aligned.
        """
        if not analysis.parameters:
            return True

        all_aligned = True
        for param in analysis.parameters:
            if param.is_aligned:
                continue

            positions = [
                p.position.strip().lower()
                for p in param.positions
                if p.position.strip().lower() != "no preference stated"
            ]

            if not positions:
                param.is_aligned = True
                continue

            if len(set(positions)) == 1:
                param.is_aligned = True
                param.aligned_value = param.positions[0].position
            else:
                all_aligned = False

        analysis.all_aligned = all_aligned
        return all_aligned

    def build_alignment_check_prompt(
        self,
        topic: str,
        current_analysis: ParameterAnalysis,
        conversation_history: str,
    ) -> str:
        """Build LLM prompt for semantic equivalence checking.

        Used as fallback when deterministic matching finds near-matches.

        Args:
            topic: Conversation topic.
            current_analysis: Current parameter state.
            conversation_history: Full conversation text.

        Returns:
            Prompt string.
        """
        params_json = current_analysis.model_dump_json(indent=2)

        return f"""You are evaluating parameter alignment in a multi-agent conversation.

Topic: {topic}

Current parameter status:
{params_json}

Conversation history:
{conversation_history}

For each parameter, determine if the agents have SEMANTICALLY agreed on the same value,
even if they used different words (e.g., "Friday AM" and "Friday morning" are the same).

IMPORTANT: Only mark a parameter as aligned if agents explicitly agreed on a SPECIFIC
value. Vague expressions like "sounds good" do NOT count unless they refer to a concrete
proposal that resolves the parameter.

Return ONLY valid JSON (no markdown fences) in the same format as the current parameter status."""

    @staticmethod
    def _extract_json(text: str) -> str | None:
        """Extract a JSON object or array from text.

        Handles markdown code fences and surrounding text.
        """
        # Try stripping markdown fences first
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if fenced:
            text = fenced.group(1).strip()

        # Find the outermost JSON object or array
        for start_char, end_char in [("{", "}"), ("[", "]")]:
            start = text.find(start_char)
            if start == -1:
                continue
            depth = 0
            for i in range(start, len(text)):
                if text[i] == start_char:
                    depth += 1
                elif text[i] == end_char:
                    depth -= 1
                    if depth == 0:
                        return text[start : i + 1]
        return None

    async def generate_summary(
        self,
        conversation_id: str,
    ) -> str:
        """Generate a summary of the completed conversation.

        Args:
            conversation_id: The conversation ID.

        Returns:
            Summary text string.
        """
        conv_data = await self._conversation_dao.get_conversation_by_id(conversation_id)
        if not conv_data:
            return "Conversation not found"

        messages = await self._conversation_dao.get_conversation_messages(conversation_id)
        participants = await self._conversation_dao.get_participants(conversation_id)

        topic = conv_data.get("topic", "Unknown")
        status = conv_data.get("status", "unknown")
        iterations = conv_data.get("current_iteration", 0)

        lines = [
            f"📋 Conversation Summary",
            f"",
            f"Topic: {topic}",
            f"Status: {status}",
            f"Iterations: {iterations}",
            f"",
            f"Participants:",
        ]

        for p in participants:
            agreement_status = "✅ Agreed" if p.get("has_agreed") else "❌ Did not agree"
            lines.append(f"  - {p.get('agent_name') or p['user_id']}: {agreement_status}")

        lines.append("")
        lines.append("Final Messages:")

        # Get last few messages from each participant
        for msg in messages[-5:]:
            sender = msg.get("participant_user_id", "Unknown")
            # Find participant name
            sender_name = sender
            for p in participants:
                if p["user_id"] == sender:
                    sender_name = p.get("agent_name") or sender
                    break
            content_preview = msg.get("content", "")[:100]
            lines.append(f"  {sender_name}: {content_preview}")

        return "\n".join(lines)


def create_conversation_manager_prompt(conversation_data: dict[str, Any]) -> str:
    """Create system prompt for conversation manager.

    Args:
        conversation_data: Dictionary with conversation state.

    Returns:
            System prompt string.
    """
    topic = conversation_data.get("topic", "")
    max_iterations = conversation_data.get("max_iterations", 5)
    current_iteration = conversation_data.get("current_iteration", 1)
    participants_info = conversation_data.get("participants_info", "")

    prompt = f"""You are the Conversation Manager for a multi-agent discussion.

Your role is to:
1. Track conversation progress (iteration {current_iteration}/{max_iterations})
2. Evaluate each agent's response for agreement or disagreement
3. Determine when all agents have agreed, reaching consensus
4. Detect when max iterations are reached
5. Generate a summary when the conversation ends

Current topic: {topic}

Participants:
{participants_info}

IMPORTANT: Be conservative in detecting agreement. Only mark an agent as agreed
when they have clearly and unambiguously agreed to the topic or proposal.

Examples of agreement:
- "I agree with that plan"
- "Sounds good, let's proceed"
- "Yes, I'm on board with Tuesday at 2PM UTC"

Examples of disagreement:
- "I don't think that will work"
- "I disagree with that approach"
- "That's not feasible"

Examples of continuing discussion (NOT agreement):
- "That's a good point, but what about..."
- "I agree, however we should consider..."
- "What if the server is down during launch?"

Return ONLY a JSON object with this exact format:
{{
    "has_agreed": true or false,
    "should_continue": true or false,
    "reason": "brief explanation of your decision"
}}"""

    return prompt
