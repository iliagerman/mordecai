"""Memory extraction service for analyzing conversations.

This service extracts important information from conversations before
session clearing, storing preferences and facts in long-term memory.

Requirements:
- 3.1: Memory_Extractor implemented as callable service method
- 3.2: Accept conversation history as input
- 3.3: Use LLM to analyze and categorize important information
- 3.4: Return structured result with preferences and facts
- 3.5: Handle empty or minimal conversations gracefully
- 3.6: Log extracted information for debugging
- 5.1: Identify user preferences (likes, dislikes, communication style)
- 5.2: Identify factual information (names, dates, project details)
- 5.3: Identify commitments and action items
- 5.4: Prioritize explicitly stated information
- 5.5: Avoid storing sensitive information
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import AgentConfig
    from app.services.memory_service import MemoryService

logger = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    """Result of memory extraction from a conversation.

    Attributes:
        preferences: User preferences identified (likes, dislikes, style).
        facts: Factual information identified (names, dates, decisions).
        commitments: Action items and commitments mentioned.
        success: Whether extraction succeeded.
        error: Error message if extraction failed.
        extraction_time_ms: Time taken for extraction in milliseconds.
    """

    preferences: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    commitments: list[str] = field(default_factory=list)
    success: bool = True
    error: str | None = None
    extraction_time_ms: int = 0


# Sensitive data patterns to filter out
SENSITIVE_PATTERNS = [
    r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
    r'\b(?:password|passwd|pwd)\s*[:=]\s*\S+',
    r'\b(?:api[_-]?key|apikey)\s*[:=]\s*\S+',
    r'\b(?:token|bearer)\s*[:=]\s*\S+',
    r'\b(?:secret|private[_-]?key)\s*[:=]\s*\S+',
    r'\b(?:sk-|pk-)[A-Za-z0-9]{20,}',
    r'\bAKIA[A-Z0-9]{16}\b',
    r'\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b',
    r'\b[0-9]{16}\b',
    r'\b(?:credit[_-]?card|card[_-]?number)\s*[:=]\s*\S+',
]

EXTRACTION_PROMPT = """Analyze the conversation and extract important info.

CATEGORIES:
1. PREFERENCES: User likes, dislikes, communication preferences, settings
2. FACTS: Names, dates, project details, decisions, technical info
3. COMMITMENTS: Action items, promises, scheduled tasks, deadlines

RULES:
- Only extract information explicitly stated by the user
- Do NOT include: passwords, API keys, tokens, personal identifiers
- Prioritize recent information over older information
- Keep each item concise (1-2 sentences max)
- If no relevant information exists for a category, return empty list
- Focus on information useful in future conversations

CONVERSATION:
{conversation}

Respond ONLY with valid JSON (no markdown, no explanation):
{{"preferences": ["item1"], "facts": ["item1"], "commitments": ["item1"]}}"""


class MemoryExtractionService:
    """Service for extracting important information from conversations.

    Analyzes conversation history using LLM to identify preferences,
    facts, and commitments, then stores them in AgentCore memory.
    """

    def __init__(
        self,
        config: "AgentConfig",
        memory_service: "MemoryService | None" = None,
    ) -> None:
        """Initialize the memory extraction service.

        Args:
            config: Application configuration.
            memory_service: Optional MemoryService for storing extractions.
        """
        self.config = config
        self.memory_service = memory_service
        self._model = None

    def _get_model(self):
        """Get or create the model for extraction."""
        if self._model is None:
            from strands.models import BedrockModel
            from strands.models.openai import OpenAIModel

            from app.enums import ModelProvider

            if self.config.model_provider == ModelProvider.OPENAI:
                if not self.config.openai_api_key:
                    raise ValueError("OpenAI API key required for extraction")
                self._model = OpenAIModel(
                    model=self.config.openai_model_id,
                    api_key=self.config.openai_api_key,
                )
            else:
                self._model = BedrockModel(
                    model_id=self.config.bedrock_model_id,
                    region_name=self.config.aws_region,
                )
        return self._model

    async def extract_and_store(
        self,
        user_id: str,
        session_id: str,
        conversation_history: list[dict],
    ) -> ExtractionResult:
        """Extract important information and store in long-term memory.

        Implements graceful degradation (Requirements 6.1, 6.2):
        - Logs errors and continues operation on extraction failures
        - Skips storage if memory service is unavailable
        - Returns ExtractionResult with success=False on errors

        Args:
            user_id: User's actor ID for memory namespacing.
            session_id: Current session identifier.
            conversation_history: List of message dicts with role and content.

        Returns:
            ExtractionResult with extracted information and status.
        """
        start_time = time.time()

        # Handle empty or minimal conversations
        if not conversation_history or len(conversation_history) < 2:
            logger.info(
                "Skipping extraction for user %s: conversation too short",
                user_id
            )
            return ExtractionResult(
                success=True,
                extraction_time_ms=int((time.time() - start_time) * 1000),
            )

        try:
            # Analyze conversation
            result = self._analyze_conversation(conversation_history)

            # Filter sensitive data
            result = self._filter_sensitive_data(result)

            # Store in memory if service available (Requirement 6.2)
            # Skip storage gracefully if memory service is unavailable
            if self.memory_service and result.success:
                try:
                    await self._store_extraction(user_id, session_id, result)
                except Exception as e:
                    # Log error but don't fail extraction (Requirement 6.1)
                    logger.error(
                        "Storage failed for user %s but extraction ok: %s",
                        user_id,
                        str(e),
                    )
            elif not self.memory_service:
                logger.warning(
                    "Memory service unavailable for user %s, "
                    "extraction complete but storage skipped",
                    user_id,
                )

            result.extraction_time_ms = int((time.time() - start_time) * 1000)

            logger.info(
                "Extraction complete for user %s: "
                "preferences=%d, facts=%d, commitments=%d, time=%dms",
                user_id,
                len(result.preferences),
                len(result.facts),
                len(result.commitments),
                result.extraction_time_ms,
            )

            return result

        except Exception as e:
            # Log error and return failure result (Requirement 6.1)
            logger.error(
                "Extraction failed for user %s: %s",
                user_id,
                str(e),
            )
            return ExtractionResult(
                success=False,
                error=str(e),
                extraction_time_ms=int((time.time() - start_time) * 1000),
            )

    def _format_conversation(
        self,
        conversation_history: list[dict],
    ) -> str:
        """Format conversation history for the extraction prompt.

        Args:
            conversation_history: List of message dicts with role and content.

        Returns:
            Formatted conversation string.
        """
        lines = []
        for msg in conversation_history:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")

            # Handle content that might be a list of blocks
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and "text" in block:
                        text_parts.append(block["text"])
                    elif isinstance(block, str):
                        text_parts.append(block)
                content = " ".join(text_parts)

            if content:
                lines.append(f"{role}: {content}")

        return "\n".join(lines)

    def _analyze_conversation(
        self,
        conversation_history: list[dict],
    ) -> ExtractionResult:
        """Use LLM to analyze conversation and categorize information.

        Args:
            conversation_history: List of message dicts with role and content.

        Returns:
            ExtractionResult with extracted categories.
        """
        from strands import Agent

        # Format conversation for prompt
        formatted_conv = self._format_conversation(conversation_history)

        # Build extraction prompt
        prompt = EXTRACTION_PROMPT.format(conversation=formatted_conv)

        try:
            # Use a simple agent call for extraction
            model = self._get_model()
            agent = Agent(
                model=model,
                system_prompt=(
                    "You are a memory extraction assistant. "
                    "Extract information and respond only with valid JSON."
                ),
            )

            result = agent(prompt)

            # Extract response text
            response_text = self._extract_response_text(result)

            # Parse JSON response
            return self._parse_extraction_response(response_text)

        except Exception as e:
            logger.error("LLM analysis failed: %s", str(e))
            return ExtractionResult(
                success=False,
                error=f"LLM analysis failed: {str(e)}",
            )

    def _extract_response_text(self, result) -> str:
        """Extract text from agent result.

        Args:
            result: Agent result object.

        Returns:
            Extracted text string.
        """
        if hasattr(result, "message") and result.message:
            content = result.message.get("content", [])
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    return block["text"]
                elif isinstance(block, str):
                    return block
        return str(result)

    def _parse_extraction_response(
        self,
        response_text: str,
    ) -> ExtractionResult:
        """Parse LLM response into ExtractionResult.

        Args:
            response_text: Raw response text from LLM.

        Returns:
            Parsed ExtractionResult.
        """
        try:
            # Clean up response - remove markdown code blocks if present
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                # Remove markdown code block markers
                lines = cleaned.split("\n")
                # Remove first line (```json or ```)
                lines = lines[1:]
                # Remove last line if it's ```
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                cleaned = "\n".join(lines)

            # Parse JSON
            data = json.loads(cleaned)

            preferences = data.get("preferences", [])
            facts = data.get("facts", [])
            commitments = data.get("commitments", [])

            # Ensure all are lists of strings
            preferences = [str(p) for p in preferences if p]
            facts = [str(f) for f in facts if f]
            commitments = [str(c) for c in commitments if c]

            logger.debug(
                "Parsed extraction: prefs=%d, facts=%d, commits=%d",
                len(preferences),
                len(facts),
                len(commitments),
            )

            return ExtractionResult(
                preferences=preferences,
                facts=facts,
                commitments=commitments,
                success=True,
            )

        except json.JSONDecodeError as e:
            logger.warning(
                "Failed to parse extraction JSON: %s. Response: %s",
                str(e),
                response_text[:200],
            )
            return ExtractionResult(
                success=False,
                error=f"Failed to parse extraction response: {str(e)}",
            )

    def _filter_sensitive_data(
        self,
        result: ExtractionResult,
    ) -> ExtractionResult:
        """Remove sensitive information from extraction results.

        Filters out passwords, API keys, tokens, personal identifiers,
        email addresses, and other sensitive data patterns.

        Args:
            result: ExtractionResult to filter.

        Returns:
            Filtered ExtractionResult with sensitive data removed.
        """
        if not result.success:
            return result

        def contains_sensitive(text: str) -> bool:
            """Check if text contains sensitive patterns."""
            text_lower = text.lower()

            # Check for sensitive keywords
            sensitive_keywords = [
                "password", "passwd", "pwd",
                "api_key", "apikey", "api-key",
                "secret", "token", "bearer",
                "private_key", "private-key",
                "access_key", "access-key",
                "credential", "auth_token",
            ]
            for keyword in sensitive_keywords:
                if keyword in text_lower:
                    return True

            # Check regex patterns
            for pattern in SENSITIVE_PATTERNS:
                if re.search(pattern, text, re.IGNORECASE):
                    return True

            return False

        def filter_list(items: list[str]) -> list[str]:
            """Filter sensitive items from a list."""
            filtered = []
            for item in items:
                if not contains_sensitive(item):
                    filtered.append(item)
                else:
                    logger.debug(
                        "Filtered sensitive item: %s...",
                        item[:30] if len(item) > 30 else item,
                    )
            return filtered

        return ExtractionResult(
            preferences=filter_list(result.preferences),
            facts=filter_list(result.facts),
            commitments=filter_list(result.commitments),
            success=result.success,
            error=result.error,
            extraction_time_ms=result.extraction_time_ms,
        )

    async def _store_extraction(
        self,
        user_id: str,
        session_id: str,
        result: ExtractionResult,
    ) -> None:
        """Store extracted information in AgentCore memory.

        Stores preferences via userPreferenceMemoryStrategy in
        /preferences/{actorId} namespace, and facts via
        semanticMemoryStrategy in /facts/{actorId} namespace.

        Gracefully handles memory service unavailability by logging
        errors and continuing operation (Requirement 6.2).

        Args:
            user_id: User's actor ID.
            session_id: Current session ID.
            result: ExtractionResult to store.
        """
        # Check memory service availability (Requirement 6.2)
        if not self.memory_service:
            logger.warning(
                "Memory service unavailable for user %s, skipping storage",
                user_id,
            )
            return

        # Verify memory service is still accessible
        try:
            # Quick check if memory service client is available
            client = self.memory_service._get_client()
            if client is None:
                logger.warning(
                    "Memory service client unavailable for user %s, "
                    "skipping storage",
                    user_id,
                )
                return
        except Exception as e:
            logger.warning(
                "Memory service unavailable for user %s: %s, skipping storage",
                user_id,
                str(e),
            )
            return

        prefs_stored = 0
        facts_stored = 0
        commits_stored = 0

        # Store preferences - continue even if this fails
        try:
            prefs_stored = await self._store_preferences(
                user_id, session_id, result.preferences
            )
        except Exception as e:
            logger.error(
                "Failed to store preferences for user %s: %s",
                user_id,
                str(e),
            )

        # Store facts - continue even if this fails
        try:
            facts_stored = await self._store_facts(
                user_id, session_id, result.facts
            )
        except Exception as e:
            logger.error(
                "Failed to store facts for user %s: %s",
                user_id,
                str(e),
            )

        # Store commitments - continue even if this fails
        try:
            commits_stored = await self._store_commitments(
                user_id, session_id, result.commitments
            )
        except Exception as e:
            logger.error(
                "Failed to store commitments for user %s: %s",
                user_id,
                str(e),
            )

        logger.info(
            "Stored extraction for user %s: "
            "prefs=%d/%d, facts=%d/%d, commits=%d/%d",
            user_id,
            prefs_stored,
            len(result.preferences),
            facts_stored,
            len(result.facts),
            commits_stored,
            len(result.commitments),
        )

    async def _store_preferences(
        self,
        user_id: str,
        session_id: str,
        preferences: list[str],
    ) -> int:
        """Store extracted preferences in AgentCore memory.

        Uses userPreferenceMemoryStrategy to store preferences in the
        /preferences/{actorId} namespace. Messages are formatted as
        user-assistant exchanges to help the strategy identify preferences.

        Args:
            user_id: User's actor ID for memory namespacing.
            session_id: Current session identifier.
            preferences: List of preference strings to store.

        Returns:
            Number of preferences successfully stored.
        """
        if not preferences or not self.memory_service:
            return 0

        stored_count = 0
        client = self.memory_service._get_client()
        memory_id = self.memory_service.get_or_create_memory_id()

        for pref in preferences:
            try:
                # Format as a conversational exchange that the
                # userPreferenceMemoryStrategy can recognize
                client.create_event(
                    memory_id=memory_id,
                    actor_id=user_id,
                    session_id=session_id,
                    messages=[
                        (f"I prefer {pref}", "USER"),
                        (
                            f"I'll remember that you prefer {pref}.",
                            "ASSISTANT"
                        ),
                    ]
                )
                stored_count += 1
                logger.debug(
                    "Stored preference for user %s: %s",
                    user_id,
                    pref[:50] if len(pref) > 50 else pref,
                )
            except Exception as e:
                logger.warning(
                    "Failed to store preference for user %s: %s",
                    user_id,
                    str(e),
                )

        return stored_count

    async def _store_facts(
        self,
        user_id: str,
        session_id: str,
        facts: list[str],
    ) -> int:
        """Store extracted facts in AgentCore memory.

        Uses semanticMemoryStrategy to store facts in the
        /facts/{actorId} namespace. Messages are formatted as
        user-assistant exchanges to help the strategy extract facts.

        Args:
            user_id: User's actor ID for memory namespacing.
            session_id: Current session identifier.
            facts: List of fact strings to store.

        Returns:
            Number of facts successfully stored.
        """
        if not facts or not self.memory_service:
            return 0

        stored_count = 0
        client = self.memory_service._get_client()
        memory_id = self.memory_service.get_or_create_memory_id()

        for fact in facts:
            try:
                # Format as a conversational exchange that the
                # semanticMemoryStrategy can recognize as factual info
                client.create_event(
                    memory_id=memory_id,
                    actor_id=user_id,
                    session_id=session_id,
                    messages=[
                        (f"Here's an important fact: {fact}", "USER"),
                        (
                            f"I've noted that {fact}.",
                            "ASSISTANT"
                        ),
                    ]
                )
                stored_count += 1
                logger.debug(
                    "Stored fact for user %s: %s",
                    user_id,
                    fact[:50] if len(fact) > 50 else fact,
                )
            except Exception as e:
                logger.warning(
                    "Failed to store fact for user %s: %s",
                    user_id,
                    str(e),
                )

        return stored_count

    async def _store_commitments(
        self,
        user_id: str,
        session_id: str,
        commitments: list[str],
    ) -> int:
        """Store extracted commitments in AgentCore memory.

        Commitments are stored as facts via semanticMemoryStrategy in the
        /facts/{actorId} namespace since they represent actionable info.

        Args:
            user_id: User's actor ID for memory namespacing.
            session_id: Current session identifier.
            commitments: List of commitment strings to store.

        Returns:
            Number of commitments successfully stored.
        """
        if not commitments or not self.memory_service:
            return 0

        stored_count = 0
        client = self.memory_service._get_client()
        memory_id = self.memory_service.get_or_create_memory_id()

        for commitment in commitments:
            try:
                # Format as a conversational exchange that the
                # semanticMemoryStrategy can recognize as actionable info
                client.create_event(
                    memory_id=memory_id,
                    actor_id=user_id,
                    session_id=session_id,
                    messages=[
                        (
                            f"I have an action item: {commitment}",
                            "USER"
                        ),
                        (
                            f"I'll remember: {commitment}.",
                            "ASSISTANT"
                        ),
                    ]
                )
                stored_count += 1
                logger.debug(
                    "Stored commitment for user %s: %s",
                    user_id,
                    commitment[:50] if len(commitment) > 50 else commitment,
                )
            except Exception as e:
                logger.warning(
                    "Failed to store commitment for user %s: %s",
                    user_id,
                    str(e),
                )

        return stored_count

    def log_extraction_summary(
        self,
        user_id: str,
        result: ExtractionResult,
    ) -> None:
        """Log a summary of extracted information for debugging.

        Args:
            user_id: User's ID.
            result: ExtractionResult to log.
        """
        if not result.success:
            logger.info(
                "Extraction failed for user %s: %s",
                user_id,
                result.error,
            )
            return

        logger.info(
            "Extraction summary for user %s:",
            user_id,
        )

        if result.preferences:
            logger.info("  Preferences (%d):", len(result.preferences))
            for pref in result.preferences[:5]:  # Log first 5
                logger.info("    - %s", pref[:100])

        if result.facts:
            logger.info("  Facts (%d):", len(result.facts))
            for fact in result.facts[:5]:  # Log first 5
                logger.info("    - %s", fact[:100])

        if result.commitments:
            logger.info("  Commitments (%d):", len(result.commitments))
            for commit in result.commitments[:5]:  # Log first 5
                logger.info("    - %s", commit[:100])

        logger.info(
            "  Extraction time: %dms",
            result.extraction_time_ms,
        )
