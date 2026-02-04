"""Memory service for AgentCore Memory integration.

This service manages AgentCore memory instances and creates session managers
for users with support for short-term memory (conversation persistence) and
long-term memory (user preferences, facts, session summaries).

Requirements:
- 1.1: Use bedrock-agentcore MemoryClient for memory operations
- 1.3: Create or retrieve memory instance on startup
- 3.2: Support summaryMemoryStrategy for session summaries
- 3.3: Support userPreferenceMemoryStrategy for user preferences
- 3.4: Support semanticMemoryStrategy for fact extraction
- 7.2: Use actor_id to namespace memory data
- 7.4: Memory strategies use namespaces that include actor_id
"""

import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)

from app.models.agent import ForgetMemoryResult, MemoryRecordMatch

from app.services.runtime_env_service import RuntimeEnvService

if TYPE_CHECKING:
    from app.config import AgentConfig

logger = logging.getLogger(__name__)


_KEY_VALUE_RE = re.compile(r"^(?P<key>[A-Za-z0-9 _\-]{1,32})\s*[:=]\s*(?P<value>.+)$")
_USER_NAME_RE = re.compile(r"\b(?:my|user)\s+name\s+is\s+(?P<name>[^.\n]{1,80})", re.IGNORECASE)
_ASSISTANT_NAME_RE = re.compile(
    r"\b(?:assistant(?:'s)?|the assistant)\s+name\s+is\s+(?P<name>[^.\n]{1,80})",
    re.IGNORECASE,
)
_TIMEZONE_RE = re.compile(r"\btime\s*zone\b\s*(?:is|:|=)\s*(?P<tz>[^.\n]{1,80})", re.IGNORECASE)


class MemoryService:
    """Service for managing AgentCore memory instances.

    Handles memory creation, retrieval, and session manager creation
    with support for multiple memory strategies and user isolation.
    """

    def __init__(
        self,
        config: "AgentConfig",
        *,
        env_service: "RuntimeEnvService | None" = None,
    ) -> None:
        """Initialize the memory service.

        Args:
            config: Application configuration with memory settings.
        """
        self.config = config
        self._client: MemoryClient | None = None
        self._memory_id: str | None = config.memory_id
        self._env = env_service or RuntimeEnvService()
        self._setup_aws_credentials()

    def _setup_aws_credentials(self) -> None:
        """Set AWS credentials for downstream AWS SDKs.

        IMPORTANT: Do not touch os.environ directly in services; we route all
        env mutation through RuntimeEnvService so it can be centralized and
        more easily tested/audited.
        """
        # IMPORTANT:
        # If the developer provides long-lived IAM credentials (access key + secret)
        # but the process environment has a stale AWS_SESSION_TOKEN set (common after
        # using STS/SSO), boto3/botocore can end up mixing them, producing confusing
        # "security token is invalid" / UnrecognizedClientException errors.
        #
        # To avoid that, when config specifies access_key_id/secret_access_key, we
        # also clear any existing session token unless config explicitly provides one.

        has_static_creds = bool(self.config.aws_access_key_id or self.config.aws_secret_access_key)

        if self.config.aws_access_key_id:
            self._env.set("AWS_ACCESS_KEY_ID", self.config.aws_access_key_id)
        if self.config.aws_secret_access_key:
            self._env.set("AWS_SECRET_ACCESS_KEY", self.config.aws_secret_access_key)

        session_token = getattr(self.config, "aws_session_token", None)
        if session_token:
            self._env.set("AWS_SESSION_TOKEN", str(session_token))
            # Some SDKs still look for AWS_SECURITY_TOKEN (legacy name)
            self._env.set("AWS_SECURITY_TOKEN", str(session_token))
        elif has_static_creds:
            # Clear stale session tokens to prevent mixed-credential failures.
            # IMPORTANT: Only do this when we are explicitly setting access/secret
            # keys from config. If the process relies on AWS_PROFILE/SSO/env-based
            # temporary credentials, we must not clear their session token.
            self._env.unset("AWS_SESSION_TOKEN")
            self._env.unset("AWS_SECURITY_TOKEN")

        if self.config.aws_region:
            # Set both for compatibility across AWS SDKs.
            self._env.set("AWS_DEFAULT_REGION", self.config.aws_region)
            self._env.set("AWS_REGION", self.config.aws_region)

    def _get_client(self) -> MemoryClient:
        """Get or create the memory client.

        Returns:
            MemoryClient instance for AgentCore operations.
        """
        if self._client is None:
            logger.debug("Creating MemoryClient for region=%s", self.config.aws_region)
            self._client = MemoryClient(region_name=self.config.aws_region)
        return self._client

    def get_or_create_memory_id(self) -> str:
        """Get existing memory ID or create new memory with strategies.

        Creates a memory instance with all three strategies:
        - summaryMemoryStrategy: Session summaries
        - userPreferenceMemoryStrategy: User preferences
        - semanticMemoryStrategy: Fact extraction

        All strategies use actor_id namespaces for user isolation.

        Returns:
            Memory ID string.

        Raises:
            Exception: If memory creation fails.
        """
        if self._memory_id:
            logger.debug("Using existing memory_id=%s", self._memory_id)
            return self._memory_id

        logger.info("Looking for existing memory: name=%s", self.config.memory_name)

        client = self._get_client()

        # Try to find existing memory by name first
        try:
            logger.info("Calling list_memories()...")
            memories = client.list_memories()
            logger.info("list_memories returned %d memories", len(memories))
            # list_memories returns a list directly
            memory_list = memories if isinstance(memories, list) else memories.get("memories", [])
            for memory in memory_list:
                # Memory ID contains the name prefix
                mem_id = memory.get("id") or memory.get("memoryId", "")
                logger.info("Checking memory: %s", mem_id)
                if mem_id.startswith(self.config.memory_name):
                    self._memory_id = mem_id
                    logger.info(
                        "Found existing memory: name=%s, id=%s",
                        self.config.memory_name,
                        self._memory_id,
                    )
                    return mem_id
        except Exception as e:
            logger.warning("Failed to list memories: %s", e)

        # Create new memory if not found
        logger.info("Creating new memory: name=%s", self.config.memory_name)
        try:
            memory = client.create_memory_and_wait(
                name=self.config.memory_name,
                description=self.config.memory_description,
                strategies=[
                    {
                        "summaryMemoryStrategy": {
                            "name": "SessionSummarizer",
                            "namespaces": ["/summaries/{actorId}/{sessionId}"],
                        }
                    },
                    {
                        "userPreferenceMemoryStrategy": {
                            "name": "PreferenceLearner",
                            "namespaces": ["/preferences/{actorId}"],
                        }
                    },
                    {
                        "semanticMemoryStrategy": {
                            "name": "FactExtractor",
                            "namespaces": ["/facts/{actorId}"],
                        }
                    },
                ],
            )
            mem_id = memory.get("id") or memory.get("memoryId")
            if not mem_id:
                raise RuntimeError("AgentCore create_memory_and_wait returned no memory id")
            self._memory_id = str(mem_id)
            logger.info("Created AgentCore memory with id=%s", self._memory_id)
        except Exception as e:
            # If creation fails due to existing memory, try to find it again
            if "already exists" in str(e):
                logger.info("Memory already exists, looking it up...")
                memories = client.list_memories()
                memory_list = (
                    memories if isinstance(memories, list) else memories.get("memories", [])
                )
                for memory in memory_list:
                    mem_id = memory.get("id") or memory.get("memoryId", "")
                    if mem_id.startswith(self.config.memory_name):
                        self._memory_id = mem_id
                        logger.info("Found memory id=%s", self._memory_id)
                        return mem_id
            raise

        if not self._memory_id:
            raise RuntimeError("MemoryService failed to determine memory_id")
        return self._memory_id

    def create_session_manager(
        self, user_id: str, session_id: str
    ) -> AgentCoreMemorySessionManager:
        """Create a session manager for a user.

        Creates an AgentCoreMemorySessionManager configured with:
        - The memory instance (created if needed)
        - User's actor_id for memory isolation
        - Session ID for conversation tracking
        - Retrieval config for all namespaces

        Args:
            user_id: User's Telegram username or ID (used as actor_id).
            session_id: Unique session identifier.

        Returns:
            Configured AgentCoreMemorySessionManager.
        """
        memory_id = self.get_or_create_memory_id()

        logger.debug("Creating session manager: user_id=%s, session_id=%s", user_id, session_id)

        # Configure retrieval from all namespaces
        retrieval_config = {
            "/preferences/{actorId}": RetrievalConfig(
                top_k=self.config.memory_retrieval_top_k,
                relevance_score=self.config.memory_retrieval_relevance_score,
            ),
            "/facts/{actorId}": RetrievalConfig(
                top_k=self.config.memory_retrieval_top_k,
                relevance_score=self.config.memory_retrieval_relevance_score,
            ),
            "/summaries/{actorId}/{sessionId}": RetrievalConfig(top_k=5, relevance_score=0.5),
        }

        config = AgentCoreMemoryConfig(
            memory_id=memory_id,
            session_id=session_id,
            actor_id=user_id,  # User isolation via actor_id
            retrieval_config=retrieval_config,
        )

        return AgentCoreMemorySessionManager(
            agentcore_memory_config=config, region_name=self.config.aws_region
        )

    @property
    def memory_id(self) -> str | None:
        """Get the current memory ID (if set)."""
        return self._memory_id

    def get_agent_identity(self, user_id: str) -> str | None:
        """Query memory for the agent's identity/name for a user.

        Searches facts and preferences namespaces for any stored name.

        Args:
            user_id: User's ID (actor_id in memory).

        Returns:
            The agent's name if found, None otherwise.
        """
        # Get or create memory_id (lazy initialization)
        try:
            memory_id = self.get_or_create_memory_id()
        except Exception as e:
            logger.warning("Cannot get agent identity: %s", e)
            return None

        client = self._get_client()

        # Search in facts namespace
        try:
            facts = client.retrieve_memories(
                memory_id=memory_id,
                namespace=f"/facts/{user_id}",
                query="assistant name identity called",
                top_k=5,
            )
            logger.info(
                "Retrieved %d facts for user %s identity lookup",
                len(facts) if facts else 0,
                user_id,
            )
            if facts:
                for fact in facts:
                    content = fact.get("content", {})
                    text = content.get("text", "") if isinstance(content, dict) else str(content)
                    logger.debug("Fact content: %s", text[:100])
                    # Look for name patterns
                    name = self._extract_name_from_text(text)
                    if name:
                        logger.info("Found agent name '%s' in facts for user %s", name, user_id)
                        return name
        except Exception as e:
            logger.warning("Failed to retrieve facts for identity: %s", e)

        # Search in preferences namespace
        try:
            prefs = client.retrieve_memories(
                memory_id=memory_id,
                namespace=f"/preferences/{user_id}",
                query="assistant name identity called",
                top_k=5,
            )
            logger.info(
                "Retrieved %d preferences for user %s identity lookup",
                len(prefs) if prefs else 0,
                user_id,
            )
            if prefs:
                for pref in prefs:
                    content = pref.get("content", {})
                    text = content.get("text", "") if isinstance(content, dict) else str(content)
                    logger.debug("Preference content: %s", text[:100])
                    name = self._extract_name_from_text(text)
                    if name:
                        logger.info("Found agent name '%s' in prefs for user %s", name, user_id)
                        return name
        except Exception as e:
            logger.warning("Failed to retrieve prefs for identity: %s", e)

        logger.info("No agent identity found in memory for user %s", user_id)
        return None

    def _extract_name_from_text(self, text: str) -> str | None:
        """Extract a name from memory text content.

        Looks for patterns like:
        - "call you X" / "call the assistant X"
        - "my name is X"
        - "name is X"
        - "called X"
        - "name: X"

        Args:
            text: Text content from memory.

        Returns:
            Extracted name or None.
        """
        import re

        text_lower = text.lower()
        logger.debug("Extracting name from text: %s", text[:200])

        # Common patterns for name assignment (order matters - specific first)
        patterns = [
            # "my name is X" - agent stating its name
            r"my name is (\w+)",
            # "call you X" - user naming the agent
            r"call you (\w+)",
            # "call me X" - agent being told its name
            r"call me (\w+)",
            # "calls their assistant X" - fact format from memory
            r"calls (?:their |the )?assistant (\w+)",
            # "call the assistant X" - user naming pattern
            r"call (?:the )?assistant (\w+)",
            # "call the assistant/agent/bot X"
            r"call (?:the )?(?:agent|bot|ai) (\w+)",
            # "assistant's name" with quotes - preference format
            r"assistant['\"]s name['\"]?\s*[:\s]+['\"]?(\w+)",
            # "assistant 'X'" - quoted name in preference
            r"(?:assistant|agent|bot)\s+['\"](\w+)['\"]",
            # "assistant/agent name is X"
            r"(?:assistant|agent|bot|ai|your) name (?:is|:) (\w+)",
            # "named the assistant X"
            r"named? (?:the )?(?:assistant|agent|bot|ai|you) (\w+)",
            # "you are X" / "you're X"
            r"(?:you are|you're|i'm calling you) (\w+)",
            # "name: X" or "name X"
            r"name[:\s]+(\w+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, text_lower)
            if match:
                name = match.group(1)
                # Skip common words that aren't names
                skip_words = {
                    "the",
                    "a",
                    "an",
                    "is",
                    "are",
                    "was",
                    "were",
                    "be",
                    "that",
                    "this",
                    "it",
                    "you",
                    "me",
                    "my",
                    "your",
                    "will",
                    "would",
                    "can",
                    "could",
                    "should",
                    "may",
                    "might",
                    "must",
                    "shall",
                    "have",
                    "has",
                    "had",
                    "do",
                    "does",
                    "did",
                    "being",
                    "been",
                    "not",
                    "no",
                }
                if name.lower() in skip_words:
                    continue
                # Capitalize the name properly
                logger.debug("Extracted name '%s' using pattern: %s", name, pattern)
                return name.capitalize()

        logger.debug("No name pattern matched in text")
        return None

    def search_memory(
        self, user_id: str, query: str, memory_type: str = "all"
    ) -> dict[str, list[str]]:
        """Search memory for specific information.

        Searches facts and/or preferences based on memory_type.
        Used by the search_memory tool for explicit memory queries.

        Args:
            user_id: User's ID (actor_id in memory).
            query: Search query string.
            memory_type: Type to search ('all', 'facts', 'preferences').

        Returns:
            Dict with 'facts' and/or 'preferences' lists.
        """
        result: dict[str, list[str]] = {"facts": [], "preferences": []}

        # Get or create memory_id (lazy initialization)
        try:
            memory_id = self.get_or_create_memory_id()
        except Exception as e:
            logger.warning("Cannot search memory: %s", e)
            return result

        client = self._get_client()

        def parse_ts(value) -> datetime | None:
            if value is None:
                return None
            if isinstance(value, datetime):
                return value
            if isinstance(value, (int, float)):
                try:
                    return datetime.fromtimestamp(float(value), tz=UTC)
                except Exception:
                    return None
            if isinstance(value, str):
                s = value.strip()
                if not s:
                    return None
                # Common AWS format: 2026-01-31T12:34:56Z
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                try:
                    dt = datetime.fromisoformat(s)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    return dt
                except Exception:
                    return None
            return None

        def record_ts(record: dict) -> datetime | None:
            # The API returns memoryRecordSummaries; timestamp field names can vary.
            for key in (
                "eventTimestamp",
                "event_timestamp",
                "createdAt",
                "created_at",
                "updatedAt",
                "updated_at",
                "timestamp",
            ):
                if key in record:
                    return parse_ts(record.get(key))
            return None

        def record_text(record: dict) -> str:
            content = record.get("content", {})
            if isinstance(content, dict):
                return str(content.get("text", "") or "")
            return str(content or "")

        def newest_first_unique(records: list[dict]) -> list[str]:
            # Deduplicate by text, keeping the newest timestamp.
            best_by_text: dict[str, datetime] = {}
            for r in records or []:
                text = record_text(r).strip()
                if not text:
                    continue
                ts = record_ts(r) or datetime.min.replace(tzinfo=UTC)
                prev = best_by_text.get(text)
                if prev is None or ts > prev:
                    best_by_text[text] = ts

            # Sort by timestamp desc (newest on top)
            return [
                text
                for text, _ts in sorted(
                    best_by_text.items(),
                    key=lambda kv: kv[1],
                    reverse=True,
                )
            ]

        # Search facts
        if memory_type in ("all", "facts"):
            try:
                facts = client.retrieve_memories(
                    memory_id=memory_id,
                    namespace=f"/facts/{user_id}",
                    query=query,
                    top_k=self.config.memory_retrieval_top_k,
                )
                if facts:
                    result["facts"] = newest_first_unique(facts)
                logger.info(
                    "Memory search found %d facts for user %s", len(result["facts"]), user_id
                )
            except Exception as e:
                logger.warning("Failed to search facts: %s", e)

        # Search preferences
        if memory_type in ("all", "preferences"):
            try:
                prefs = client.retrieve_memories(
                    memory_id=memory_id,
                    namespace=f"/preferences/{user_id}",
                    query=query,
                    top_k=self.config.memory_retrieval_top_k,
                )
                if prefs:
                    result["preferences"] = newest_first_unique(prefs)
                logger.info(
                    "Memory search found %d preferences for user %s",
                    len(result["preferences"]),
                    user_id,
                )
            except Exception as e:
                logger.warning("Failed to search preferences: %s", e)

        return result

    def retrieve_memory_context(self, user_id: str, query: str) -> dict[str, list[str]]:
        """Retrieve relevant memory context for a user based on their query.

        Queries all namespaces (facts, preferences) using the user's message
        to find semantically relevant memories.

        Args:
            user_id: User's ID (actor_id in memory).
            query: The user's message to search for relevant memories.

        Returns:
            Dict with keys 'facts', 'preferences' containing lists of
            memory text content, plus 'agent_name' if found.
        """
        result = {
            "facts": [],
            "preferences": [],
            "agent_name": None,
        }

        # Get or create memory_id (lazy initialization)
        try:
            memory_id = self.get_or_create_memory_id()
        except Exception as e:
            logger.warning("Cannot retrieve memory context: %s", e)
            return result

        client = self._get_client()

        def parse_ts(value) -> datetime | None:
            if value is None:
                return None
            if isinstance(value, datetime):
                return value
            if isinstance(value, (int, float)):
                try:
                    return datetime.fromtimestamp(float(value), tz=UTC)
                except Exception:
                    return None
            if isinstance(value, str):
                s = value.strip()
                if not s:
                    return None
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                try:
                    dt = datetime.fromisoformat(s)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    return dt
                except Exception:
                    return None
            return None

        def record_ts(record: dict) -> datetime | None:
            for key in (
                "eventTimestamp",
                "event_timestamp",
                "createdAt",
                "created_at",
                "updatedAt",
                "updated_at",
                "timestamp",
            ):
                if key in record:
                    return parse_ts(record.get(key))
            return None

        def record_text(record: dict) -> str:
            content = record.get("content", {})
            if isinstance(content, dict):
                return str(content.get("text", "") or "")
            return str(content or "")

        def newest_first_records(records: list[dict]) -> list[tuple[str, datetime]]:
            best_by_text: dict[str, datetime] = {}
            for r in records or []:
                text = record_text(r).strip()
                if not text:
                    continue
                ts = record_ts(r) or datetime.min.replace(tzinfo=UTC)
                prev = best_by_text.get(text)
                if prev is None or ts > prev:
                    best_by_text[text] = ts

            return sorted(best_by_text.items(), key=lambda kv: kv[1], reverse=True)

        # Retrieve facts relevant to the user's query
        try:
            facts = client.retrieve_memories(
                memory_id=memory_id,
                namespace=f"/facts/{user_id}",
                query=query,
                top_k=self.config.memory_retrieval_top_k,
            )
            if facts:
                for text, _ts in newest_first_records(facts):
                    logger.info("Fact text: %s", text[:200] if text else "")
                    result["facts"].append(text)
                    if not result["agent_name"]:
                        name = self._extract_name_from_text(text)
                        if name:
                            result["agent_name"] = name
                logger.info(
                    "Retrieved %d facts for user %s (query: %s)",
                    len(result["facts"]),
                    user_id,
                    query[:50],
                )
        except Exception as e:
            logger.warning("Failed to retrieve facts: %s", e)

        # Retrieve preferences relevant to the user's query
        try:
            prefs = client.retrieve_memories(
                memory_id=memory_id,
                namespace=f"/preferences/{user_id}",
                query=query,
                top_k=self.config.memory_retrieval_top_k,
            )
            if prefs:
                for text, _ts in newest_first_records(prefs):
                    logger.info("Pref text: %s", text[:200] if text else "")
                    result["preferences"].append(text)
                    if not result["agent_name"]:
                        name = self._extract_name_from_text(text)
                        if name:
                            result["agent_name"] = name
                logger.info(
                    "Retrieved %d preferences for user %s", len(result["preferences"]), user_id
                )
        except Exception as e:
            logger.warning("Failed to retrieve preferences: %s", e)

        # Also do a specific identity query if asking about name
        if not result["agent_name"] and any(
            kw in query.lower() for kw in ["name", "who are you", "identity"]
        ):
            logger.info("Doing identity-specific query for user %s", user_id)
            # Search facts for identity
            try:
                identity_facts = client.retrieve_memories(
                    memory_id=memory_id,
                    namespace=f"/facts/{user_id}",
                    query="assistant name identity called my name is",
                    top_k=5,
                )
                logger.info(
                    "Identity facts query returned %d results",
                    len(identity_facts) if identity_facts else 0,
                )
                if identity_facts:
                    for text, _ts in newest_first_records(identity_facts):
                        logger.info("Identity fact text: %s", text[:200])
                        name = self._extract_name_from_text(text)
                        if name:
                            result["agent_name"] = name
                            if text not in result["facts"]:
                                result["facts"].append(text)
                            break
            except Exception as e:
                logger.warning("Failed identity lookup in facts: %s", e)

            # Also search preferences for identity if not found in facts
            if not result["agent_name"]:
                try:
                    identity_prefs = client.retrieve_memories(
                        memory_id=memory_id,
                        namespace=f"/preferences/{user_id}",
                        query="assistant name identity called my name is",
                        top_k=5,
                    )
                    logger.info(
                        "Identity prefs query returned %d results",
                        len(identity_prefs) if identity_prefs else 0,
                    )
                    if identity_prefs:
                        for text, _ts in newest_first_records(identity_prefs):
                            logger.info("Identity pref text: %s", text[:200])
                            name = self._extract_name_from_text(text)
                            if name:
                                result["agent_name"] = name
                                if text not in result["preferences"]:
                                    result["preferences"].append(text)
                                break
                except Exception as e:
                    logger.warning("Failed identity lookup in prefs: %s", e)

        # Merge in Obsidian short-term memory (authoritative) when configured.
        try:
            result = self._merge_short_term_over_long_term(user_id, result)
        except Exception as e:
            logger.debug("Short-term memory merge skipped for user %s: %s", user_id, e)

        logger.info(
            "Final memory context: agent_name=%s, facts=%d, prefs=%d",
            result["agent_name"],
            len(result["facts"]),
            len(result["preferences"]),
        )
        return result

    def _normalize_overwrite_key(self, key: str) -> str:
        key = (key or "").strip().lower()
        key = re.sub(r"\s+", "_", key)
        key = re.sub(r"[^a-z0-9_\-]", "", key)
        return key

    def _extract_overwrite_key(self, text: str) -> str | None:
        """Extract a deterministic overwrite-key from a memory string.

        This is intentionally conservative. If we can't extract a stable key,
        we return None and fall back to exact-text dedupe.
        """

        t = (text or "").strip()
        if not t:
            return None

        # Name facts (high-value, single-valued)
        m = _ASSISTANT_NAME_RE.search(t)
        if m:
            return "assistant_name"
        m = _USER_NAME_RE.search(t)
        if m:
            return "user_name"

        # Timezone-like facts/preferences
        if _TIMEZONE_RE.search(t):
            return "timezone"

        # Explicit key/value format: "Key: Value" or "Key = Value"
        m = _KEY_VALUE_RE.match(t)
        if m:
            key = self._normalize_overwrite_key(m.group("key"))
            return key or None

        return None

    def _merge_short_term_over_long_term(
        self,
        user_id: str,
        long_term: dict,
    ) -> dict:
        """Merge short-term (Obsidian) memories over long-term AgentCore memories.

        Conflict semantics:
        - If a short-term and long-term memory share the same overwrite-key,
          the short-term memory wins and the long-term one is suppressed.
        - If no overwrite-key can be extracted, we only de-dupe by normalized
          exact text.

        The merged result lists are ordered with short-term items first.
        """

        vault_root = getattr(self.config, "obsidian_vault_root", None)
        if not vault_root:
            return long_term

        try:
            from app.tools.short_term_memory_vault import read_parsed
        except Exception:
            return long_term

        stm = read_parsed(vault_root, user_id)
        if not stm:
            return long_term

        def norm_text(s: str) -> str:
            return re.sub(r"\s+", " ", (s or "").strip().lower())

        def merge_lists(stm_list: list[str], ltm_list: list[str]) -> list[str]:
            stm_items = [
                (s, self._extract_overwrite_key(s), norm_text(s)) for s in (stm_list or [])
            ]
            ltm_items = [
                (s, self._extract_overwrite_key(s), norm_text(s)) for s in (ltm_list or [])
            ]

            # Keys that exist in short-term (these overwrite long-term)
            stm_keys = {k for _s, k, _n in stm_items if k}

            merged: list[str] = []
            seen_norm: set[str] = set()

            # Add STM first
            for s, _k, n in stm_items:
                if not s:
                    continue
                if n in seen_norm:
                    continue
                seen_norm.add(n)
                merged.append(s)

            # Add LTM, suppressing conflicts
            for s, k, n in ltm_items:
                if not s:
                    continue
                if n in seen_norm:
                    continue
                if k and k in stm_keys:
                    continue
                seen_norm.add(n)
                merged.append(s)

            return merged

        merged = dict(long_term)
        merged["facts"] = merge_lists(stm.facts, long_term.get("facts", []))
        merged["preferences"] = merge_lists(stm.preferences, long_term.get("preferences", []))

        # If STM has an assistant name (or any name), it should take precedence.
        if not merged.get("agent_name"):
            for candidate in merged.get("facts", []) + merged.get("preferences", []):
                name = self._extract_name_from_text(str(candidate))
                if name:
                    merged["agent_name"] = name
                    break

        return merged

    def _find_similar_records(
        self, user_id: str, query: str, similarity_threshold: float = 0.0
    ) -> list[dict]:
        """Find memory records similar to a query.

        Uses semantic search to find records that might be about the same
        topic, which could need to be replaced.

        Args:
            user_id: User's ID (actor_id in memory).
            query: Search query to find similar records. Use '*' to get all records.
            similarity_threshold: Minimum relevance score (0-1). Defaults to 0.0.

        Returns:
            List of record dicts with memoryRecordId, text, score.
        """
        try:
            memory_id = self.get_or_create_memory_id()
        except Exception as e:
            logger.warning("Cannot find similar records: %s", e)
            return []

        client = self._get_client()
        similar_records = []

        # Search both facts and preferences
        namespaces = [f"/facts/{user_id}", f"/preferences/{user_id}"]
        is_wildcard = query.strip() == "*"

        for namespace in namespaces:
            try:
                if is_wildcard:
                    # For wildcard, list all records (no search query, higher topK)
                    response = client.gmdp_client.retrieve_memory_records(
                        memoryId=memory_id,
                        namespace=namespace,
                        searchCriteria={"searchQuery": "", "topK": 100},
                    )
                else:
                    response = client.gmdp_client.retrieve_memory_records(
                        memoryId=memory_id,
                        namespace=namespace,
                        searchCriteria={"searchQuery": query, "topK": 50},
                    )

                for summary in response.get("memoryRecordSummaries", []):
                    score = summary.get("score", 0)
                    # For wildcard, accept all records; otherwise filter by threshold
                    if is_wildcard or score >= similarity_threshold:
                        record_id = summary.get("memoryRecordId")
                        content = summary.get("content", {})
                        text = content.get("text", "")
                        if record_id:
                            similar_records.append(
                                {
                                    "memoryRecordId": record_id,
                                    "text": text,
                                    "score": score,
                                    "namespace": namespace,
                                }
                            )
            except Exception as e:
                logger.warning("Failed to search namespace %s: %s", namespace, e)

        return similar_records

    def _delete_records(self, record_ids: list[str]) -> int:
        """Delete memory records by their IDs.

        Args:
            record_ids: List of memory record IDs to delete.

        Returns:
            Number of records successfully deleted.
        """
        if not record_ids:
            return 0

        try:
            memory_id = self.get_or_create_memory_id()
        except Exception as e:
            logger.warning("Cannot delete records: %s", e)
            return 0

        client = self._get_client()
        deleted = 0

        for record_id in record_ids:
            try:
                client.gmdp_client.delete_memory_record(
                    memoryId=memory_id, memoryRecordId=record_id
                )
                deleted += 1
                logger.debug("Deleted memory record %s", record_id)
            except Exception as e:
                logger.warning("Failed to delete record %s: %s", record_id, e)

        if deleted > 0:
            logger.info("Deleted %d memory records", deleted)

        return deleted

    def delete_similar_records(
        self,
        *,
        user_id: str,
        query: str,
        memory_type: str = "all",
        similarity_threshold: float = 0.0,
        dry_run: bool = True,
        max_matches: int = 100,
    ) -> ForgetMemoryResult:
        """Delete AgentCore memory records similar to a query.

        This is intended for "forget" flows when a stored fact/preference is
        wrong or outdated.

        Safety:
        - Default is dry-run (no deletes).
        - Matches are limited to max_matches.

        Args:
            user_id: Actor ID (namespacing).
            query: Search query to find records to delete.
            memory_type: 'facts', 'preferences', or 'all'.
            similarity_threshold: Minimum relevance score (0-1).
            dry_run: If True, do not delete; only report matches.
            max_matches: Cap number of records considered for deletion.

        Returns:
            ForgetMemoryResult with match previews and deleted count.
        """

        q = (query or "").strip()
        mt = (memory_type or "all").strip().lower()
        if mt not in ("all", "facts", "preferences"):
            mt = "all"

        result = ForgetMemoryResult(
            user_id=user_id,
            query=q,
            memory_type=mt,
            similarity_threshold=float(similarity_threshold),
            dry_run=bool(dry_run),
        )

        if not q:
            return result

        # Find similar records using AgentCore search.
        matches = self._find_similar_records(
            user_id=user_id,
            query=q,
            similarity_threshold=float(similarity_threshold),
        )

        # Filter by requested memory_type.
        if mt == "facts":
            matches = [m for m in matches if str(m.get("namespace", "")).startswith("/facts/")]
        elif mt == "preferences":
            matches = [
                m for m in matches if str(m.get("namespace", "")).startswith("/preferences/")
            ]

        if max_matches > 0:
            matches = matches[: int(max_matches)]

        # Build typed previews (avoid leaking full text by default).
        typed: list[MemoryRecordMatch] = []
        for m in matches:
            rec_id = str(m.get("memoryRecordId") or "").strip()
            ns = str(m.get("namespace") or "").strip()
            text = str(m.get("text") or "")
            typed.append(
                MemoryRecordMatch(
                    memory_record_id=rec_id,
                    namespace=ns,
                    score=float(m.get("score") or 0.0),
                    text_preview=(text[:200] + ("…" if len(text) > 200 else "")),
                )
            )

        result.matches = typed
        result.matched = len(typed)

        if result.dry_run or not typed:
            return result

        deleted = self._delete_records([t.memory_record_id for t in typed if t.memory_record_id])
        result.deleted = int(deleted)
        return result

    def store_fact(
        self,
        user_id: str,
        fact: str,
        session_id: str,
        replace_similar: bool = True,
        similarity_query: str | None = None,
        *,
        write_to_short_term: bool = False,
        short_term_kind: str = "fact",
    ) -> bool:
        """Store a fact in memory, optionally replacing similar facts.

        This is the general method for storing facts that may change over
        time. It searches for similar existing facts and deletes them
        before storing the new one.

        Args:
            user_id: User's ID (actor_id in memory).
            fact: The fact to store.
            session_id: Current session ID.
            replace_similar: If True, delete similar existing facts first.
            similarity_query: Custom query to find similar facts.
                If None, uses the fact itself as the query.

        Returns:
            True if stored successfully, False otherwise.
        """
        ltm_ok = False
        stm_ok = False

        # ------------------------------------------------------------------
        # Best-effort long-term memory write (AgentCore)
        # ------------------------------------------------------------------
        # If AgentCore is misconfigured/unavailable (common in local dev), we
        # still want explicit "remember" requests to persist to the Obsidian
        # short-term memory note when configured.
        try:
            memory_id = self.get_or_create_memory_id()
            client = self._get_client()

            # Find and delete similar records if requested
            if replace_similar:
                query = similarity_query or fact
                similar = self._find_similar_records(user_id, query)
                if similar:
                    record_ids = [r["memoryRecordId"] for r in similar]
                    logger.info(
                        "Found %d similar records to replace for user %s", len(similar), user_id
                    )
                    for r in similar:
                        logger.debug(
                            "  - %s (score=%.2f): %s",
                            r["memoryRecordId"],
                            r["score"],
                            r["text"][:100],
                        )
                    self._delete_records(record_ids)

            # Create event to store the fact
            client.create_event(
                memory_id=memory_id,
                actor_id=user_id,
                session_id=session_id,
                event_timestamp=datetime.now(UTC),
                messages=[
                    (f"Important fact: {fact}", "USER"),
                    (f"I'll remember that: {fact}", "ASSISTANT"),
                ],
            )
            logger.info("Stored fact for user %s: %s", user_id, fact[:100])
            ltm_ok = True
        except Exception as e:
            logger.warning(
                "Long-term memory store unavailable for user %s (will try STM if enabled): %s",
                user_id,
                e,
            )

        # ------------------------------------------------------------------
        # Best-effort short-term memory write (Obsidian vault)
        # ------------------------------------------------------------------
        if write_to_short_term:
            vault_root = getattr(self.config, "obsidian_vault_root", None)
            if vault_root:
                try:
                    from app.tools.short_term_memory_vault import append_memory

                    append_memory(
                        vault_root,
                        user_id,
                        kind=short_term_kind or "fact",
                        text=fact,
                        max_chars=getattr(self.config, "personality_max_chars", 20_000),
                    )
                    stm_ok = True
                except Exception as e:
                    # Don't fail if STM append fails; just report False if we
                    # also failed to store LTM.
                    logger.debug(
                        "Failed to append short-term memory for user %s: %s",
                        user_id,
                        e,
                    )

        return bool(ltm_ok or stm_ok)

    def store_preference(
        self,
        user_id: str,
        preference: str,
        session_id: str,
        *,
        write_to_short_term: bool = False,
    ) -> bool:
        """Store a user preference in memory.

        Uses the userPreferenceMemoryStrategy by formatting the event as a
        user/assistant exchange that clearly expresses a preference.

        Args:
            user_id: User's ID (actor_id in memory).
            preference: The preference to store.
            session_id: Current session ID.

        Returns:
            True if stored successfully, False otherwise.
        """
        preference = preference.strip() if preference else ""
        if not preference:
            return False

        ltm_ok = False
        stm_ok = False

        # Long-term memory (AgentCore) is best-effort.
        try:
            memory_id = self.get_or_create_memory_id()
            client = self._get_client()

            client.create_event(
                memory_id=memory_id,
                actor_id=user_id,
                session_id=session_id,
                event_timestamp=datetime.now(UTC),
                messages=[
                    (f"I prefer {preference}", "USER"),
                    (
                        f"I'll remember that you prefer {preference}.",
                        "ASSISTANT",
                    ),
                ],
            )
            logger.info(
                "Stored preference for user %s: %s",
                user_id,
                preference[:100],
            )
            ltm_ok = True
        except Exception as e:
            logger.warning(
                "Long-term preference store unavailable for user %s (will try STM if enabled): %s",
                user_id,
                e,
            )

        # Short-term memory note write (Obsidian) is best-effort.
        if write_to_short_term:
            vault_root = getattr(self.config, "obsidian_vault_root", None)
            if vault_root:
                try:
                    from app.tools.short_term_memory_vault import append_memory

                    append_memory(
                        vault_root,
                        user_id,
                        kind="preference",
                        text=preference,
                        max_chars=getattr(self.config, "personality_max_chars", 20_000),
                    )
                    stm_ok = True
                except Exception as e:
                    logger.debug(
                        "Failed to append short-term preference for user %s: %s",
                        user_id,
                        e,
                    )

        return bool(ltm_ok or stm_ok)

    def store_session_summary(
        self,
        user_id: str,
        session_id: str,
        summary: str,
    ) -> bool:
        """Store a session summary in long-term memory.

        We store the summary as a fact so it is retrievable via the existing
        /facts/{actorId} retrieval logic. The similarity query scopes replacement
        to the given session_id to avoid overwriting other sessions.

        Args:
            user_id: User's ID (actor_id in memory).
            session_id: Session identifier being summarized.
            summary: Summary text.

        Returns:
            True if stored successfully, False otherwise.
        """
        summary = summary.strip() if summary else ""
        if not summary:
            return False

        fact = f"Session summary for {session_id}: {summary}"
        similarity_query = f"session summary {session_id}"

        # Replace only summaries for this same session.
        return self.store_fact(
            user_id=user_id,
            fact=fact,
            session_id=session_id,
            replace_similar=True,
            similarity_query=similarity_query,
        )

    def store_agent_name(self, user_id: str, name: str, session_id: str) -> bool:
        """Store the agent's name in memory for a user.

        Uses store_fact with a specific similarity query to find and
        replace any existing name records.

        Args:
            user_id: User's ID (actor_id in memory).
            name: The name to store for the agent.
            session_id: Current session ID.

        Returns:
            True if stored successfully, False otherwise.
        """
        # Use store_fact with a query that will find name-related records
        fact = f"The assistant's name is {name}. Call the assistant {name}."
        similarity_query = "assistant name called identify"

        return self.store_fact(
            user_id=user_id,
            fact=fact,
            session_id=session_id,
            replace_similar=True,
            similarity_query=similarity_query,
        )
