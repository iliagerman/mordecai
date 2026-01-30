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
import os
from typing import TYPE_CHECKING

from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)

if TYPE_CHECKING:
    from app.config import AgentConfig

logger = logging.getLogger(__name__)


class MemoryService:
    """Service for managing AgentCore memory instances.

    Handles memory creation, retrieval, and session manager creation
    with support for multiple memory strategies and user isolation.
    """

    def __init__(self, config: "AgentConfig") -> None:
        """Initialize the memory service.

        Args:
            config: Application configuration with memory settings.
        """
        self.config = config
        self._client: MemoryClient | None = None
        self._memory_id: str | None = config.memory_id
        self._setup_aws_credentials()

    def _setup_aws_credentials(self) -> None:
        """Set AWS credentials as environment variables if provided."""
        if self.config.aws_access_key_id:
            os.environ["AWS_ACCESS_KEY_ID"] = self.config.aws_access_key_id
        if self.config.aws_secret_access_key:
            os.environ["AWS_SECRET_ACCESS_KEY"] = (
                self.config.aws_secret_access_key
            )
        if self.config.aws_region:
            os.environ["AWS_DEFAULT_REGION"] = self.config.aws_region

    def _get_client(self) -> MemoryClient:
        """Get or create the memory client.

        Returns:
            MemoryClient instance for AgentCore operations.
        """
        if self._client is None:
            logger.debug(
                "Creating MemoryClient for region=%s",
                self.config.aws_region
            )
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

        logger.info(
            "Looking for existing memory: name=%s",
            self.config.memory_name
        )

        client = self._get_client()

        # Try to find existing memory by name first
        try:
            logger.info("Calling list_memories()...")
            memories = client.list_memories()
            logger.info("list_memories returned %d memories", len(memories))
            # list_memories returns a list directly
            memory_list = memories if isinstance(memories, list) else \
                memories.get("memories", [])
            for memory in memory_list:
                # Memory ID contains the name prefix
                mem_id = memory.get("id") or memory.get("memoryId", "")
                logger.info("Checking memory: %s", mem_id)
                if mem_id.startswith(self.config.memory_name):
                    self._memory_id = mem_id
                    logger.info(
                        "Found existing memory: name=%s, id=%s",
                        self.config.memory_name,
                        self._memory_id
                    )
                    return self._memory_id
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
                            "namespaces": ["/summaries/{actorId}/{sessionId}"]
                        }
                    },
                    {
                        "userPreferenceMemoryStrategy": {
                            "name": "PreferenceLearner",
                            "namespaces": ["/preferences/{actorId}"]
                        }
                    },
                    {
                        "semanticMemoryStrategy": {
                            "name": "FactExtractor",
                            "namespaces": ["/facts/{actorId}"]
                        }
                    }
                ]
            )
            self._memory_id = memory.get("id")
            logger.info("Created AgentCore memory with id=%s", self._memory_id)
        except Exception as e:
            # If creation fails due to existing memory, try to find it again
            if "already exists" in str(e):
                logger.info("Memory already exists, looking it up...")
                memories = client.list_memories()
                memory_list = memories if isinstance(memories, list) else \
                    memories.get("memories", [])
                for memory in memory_list:
                    mem_id = memory.get("id") or memory.get("memoryId", "")
                    if mem_id.startswith(self.config.memory_name):
                        self._memory_id = mem_id
                        logger.info("Found memory id=%s", self._memory_id)
                        return self._memory_id
            raise

        return self._memory_id

    def create_session_manager(
        self,
        user_id: str,
        session_id: str
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

        logger.debug(
            "Creating session manager: user_id=%s, session_id=%s",
            user_id,
            session_id
        )

        # Configure retrieval from all namespaces
        retrieval_config = {
            "/preferences/{actorId}": RetrievalConfig(
                top_k=self.config.memory_retrieval_top_k,
                relevance_score=self.config.memory_retrieval_relevance_score
            ),
            "/facts/{actorId}": RetrievalConfig(
                top_k=self.config.memory_retrieval_top_k,
                relevance_score=self.config.memory_retrieval_relevance_score
            ),
            "/summaries/{actorId}/{sessionId}": RetrievalConfig(
                top_k=5,
                relevance_score=0.5
            )
        }

        config = AgentCoreMemoryConfig(
            memory_id=memory_id,
            session_id=session_id,
            actor_id=user_id,  # User isolation via actor_id
            retrieval_config=retrieval_config
        )

        return AgentCoreMemorySessionManager(
            agentcore_memory_config=config,
            region_name=self.config.aws_region
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
                top_k=5
            )
            logger.info(
                "Retrieved %d facts for user %s identity lookup",
                len(facts) if facts else 0,
                user_id
            )
            if facts:
                for fact in facts:
                    content = fact.get("content", {})
                    text = content.get("text", "") if isinstance(
                        content, dict
                    ) else str(content)
                    logger.debug("Fact content: %s", text[:100])
                    # Look for name patterns
                    name = self._extract_name_from_text(text)
                    if name:
                        logger.info(
                            "Found agent name '%s' in facts for user %s",
                            name,
                            user_id
                        )
                        return name
        except Exception as e:
            logger.warning("Failed to retrieve facts for identity: %s", e)

        # Search in preferences namespace
        try:
            prefs = client.retrieve_memories(
                memory_id=memory_id,
                namespace=f"/preferences/{user_id}",
                query="assistant name identity called",
                top_k=5
            )
            logger.info(
                "Retrieved %d preferences for user %s identity lookup",
                len(prefs) if prefs else 0,
                user_id
            )
            if prefs:
                for pref in prefs:
                    content = pref.get("content", {})
                    text = content.get("text", "") if isinstance(
                        content, dict
                    ) else str(content)
                    logger.debug("Preference content: %s", text[:100])
                    name = self._extract_name_from_text(text)
                    if name:
                        logger.info(
                            "Found agent name '%s' in prefs for user %s",
                            name,
                            user_id
                        )
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
                    "the", "a", "an", "is", "are", "was", "were", "be",
                    "that", "this", "it", "you", "me", "my", "your",
                    "will", "would", "can", "could", "should", "may",
                    "might", "must", "shall", "have", "has", "had",
                    "do", "does", "did", "being", "been", "not", "no",
                }
                if name.lower() in skip_words:
                    continue
                # Capitalize the name properly
                logger.debug(
                    "Extracted name '%s' using pattern: %s", name, pattern
                )
                return name.capitalize()

        logger.debug("No name pattern matched in text")
        return None

    def search_memory(
        self,
        user_id: str,
        query: str,
        memory_type: str = "all"
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

        # Search facts
        if memory_type in ("all", "facts"):
            try:
                facts = client.retrieve_memories(
                    memory_id=memory_id,
                    namespace=f"/facts/{user_id}",
                    query=query,
                    top_k=self.config.memory_retrieval_top_k
                )
                if facts:
                    for fact in facts:
                        content = fact.get("content", {})
                        text = content.get("text", "") if isinstance(
                            content, dict
                        ) else str(content)
                        if text and text not in result["facts"]:
                            result["facts"].append(text)
                logger.info(
                    "Memory search found %d facts for user %s",
                    len(result["facts"]),
                    user_id
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
                    top_k=self.config.memory_retrieval_top_k
                )
                if prefs:
                    for pref in prefs:
                        content = pref.get("content", {})
                        text = content.get("text", "") if isinstance(
                            content, dict
                        ) else str(content)
                        if text and text not in result["preferences"]:
                            result["preferences"].append(text)
                logger.info(
                    "Memory search found %d preferences for user %s",
                    len(result["preferences"]),
                    user_id
                )
            except Exception as e:
                logger.warning("Failed to search preferences: %s", e)

        return result

    def retrieve_memory_context(
        self,
        user_id: str,
        query: str
    ) -> dict[str, list[str]]:
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

        # Retrieve facts relevant to the user's query
        try:
            facts = client.retrieve_memories(
                memory_id=memory_id,
                namespace=f"/facts/{user_id}",
                query=query,
                top_k=self.config.memory_retrieval_top_k
            )
            if facts:
                for fact in facts:
                    content = fact.get("content", {})
                    text = content.get("text", "") if isinstance(
                        content, dict
                    ) else str(content)
                    logger.debug("Fact raw content: %s", fact)
                    logger.info("Fact text: %s", text[:200] if text else "")
                    if text:
                        result["facts"].append(text)
                        # Check for agent name
                        if not result["agent_name"]:
                            name = self._extract_name_from_text(text)
                            if name:
                                result["agent_name"] = name
                logger.info(
                    "Retrieved %d facts for user %s (query: %s)",
                    len(result["facts"]),
                    user_id,
                    query[:50]
                )
        except Exception as e:
            logger.warning("Failed to retrieve facts: %s", e)

        # Retrieve preferences relevant to the user's query
        try:
            prefs = client.retrieve_memories(
                memory_id=memory_id,
                namespace=f"/preferences/{user_id}",
                query=query,
                top_k=self.config.memory_retrieval_top_k
            )
            if prefs:
                for pref in prefs:
                    content = pref.get("content", {})
                    text = content.get("text", "") if isinstance(
                        content, dict
                    ) else str(content)
                    logger.debug("Pref raw content: %s", pref)
                    logger.info("Pref text: %s", text[:200] if text else "")
                    if text:
                        result["preferences"].append(text)
                        # Check for agent name
                        if not result["agent_name"]:
                            name = self._extract_name_from_text(text)
                            if name:
                                result["agent_name"] = name
                logger.info(
                    "Retrieved %d preferences for user %s",
                    len(result["preferences"]),
                    user_id
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
                    top_k=5
                )
                logger.info(
                    "Identity facts query returned %d results",
                    len(identity_facts) if identity_facts else 0
                )
                if identity_facts:
                    for fact in identity_facts:
                        content = fact.get("content", {})
                        text = content.get("text", "") if isinstance(
                            content, dict
                        ) else str(content)
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
                        top_k=5
                    )
                    logger.info(
                        "Identity prefs query returned %d results",
                        len(identity_prefs) if identity_prefs else 0
                    )
                    if identity_prefs:
                        for pref in identity_prefs:
                            content = pref.get("content", {})
                            text = content.get("text", "") if isinstance(
                                content, dict
                            ) else str(content)
                            logger.info("Identity pref text: %s", text[:200])
                            name = self._extract_name_from_text(text)
                            if name:
                                result["agent_name"] = name
                                if text not in result["preferences"]:
                                    result["preferences"].append(text)
                                break
                except Exception as e:
                    logger.warning("Failed identity lookup in prefs: %s", e)

        logger.info(
            "Final memory context: agent_name=%s, facts=%d, prefs=%d",
            result["agent_name"],
            len(result["facts"]),
            len(result["preferences"])
        )
        return result

    def _find_similar_records(
        self,
        user_id: str,
        query: str,
        similarity_threshold: float = 0.7
    ) -> list[dict]:
        """Find memory records similar to a query.

        Uses semantic search to find records that might be about the same
        topic, which could need to be replaced.

        Args:
            user_id: User's ID (actor_id in memory).
            query: Search query to find similar records.
            similarity_threshold: Minimum relevance score (0-1).

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

        for namespace in namespaces:
            try:
                response = client.gmdp_client.retrieve_memory_records(
                    memoryId=memory_id,
                    namespace=namespace,
                    searchCriteria={
                        "searchQuery": query,
                        "topK": 10
                    }
                )

                for summary in response.get("memoryRecordSummaries", []):
                    score = summary.get("score", 0)
                    if score >= similarity_threshold:
                        record_id = summary.get("memoryRecordId")
                        content = summary.get("content", {})
                        text = content.get("text", "")
                        if record_id:
                            similar_records.append({
                                "memoryRecordId": record_id,
                                "text": text,
                                "score": score,
                                "namespace": namespace
                            })
            except Exception as e:
                logger.warning(
                    "Failed to search namespace %s: %s", namespace, e
                )

        return similar_records

    def _delete_records(
        self,
        record_ids: list[str]
    ) -> int:
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
                    memoryId=memory_id,
                    memoryRecordId=record_id
                )
                deleted += 1
                logger.debug("Deleted memory record %s", record_id)
            except Exception as e:
                logger.warning(
                    "Failed to delete record %s: %s", record_id, e
                )

        if deleted > 0:
            logger.info("Deleted %d memory records", deleted)

        return deleted

    def store_fact(
        self,
        user_id: str,
        fact: str,
        session_id: str,
        replace_similar: bool = True,
        similarity_query: str | None = None
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
        try:
            memory_id = self.get_or_create_memory_id()
        except Exception as e:
            logger.warning("Cannot store fact: %s", e)
            return False

        client = self._get_client()

        # Find and delete similar records if requested
        if replace_similar:
            query = similarity_query or fact
            similar = self._find_similar_records(user_id, query)
            if similar:
                record_ids = [r["memoryRecordId"] for r in similar]
                logger.info(
                    "Found %d similar records to replace for user %s",
                    len(similar), user_id
                )
                for r in similar:
                    logger.debug(
                        "  - %s (score=%.2f): %s",
                        r["memoryRecordId"],
                        r["score"],
                        r["text"][:100]
                    )
                self._delete_records(record_ids)

        try:
            # Create event to store the fact
            client.create_event(
                memory_id=memory_id,
                actor_id=user_id,
                session_id=session_id,
                messages=[
                    (f"Important fact: {fact}", "USER"),
                    (f"I'll remember that: {fact}", "ASSISTANT"),
                ]
            )
            logger.info("Stored fact for user %s: %s", user_id, fact[:100])
            return True
        except Exception as e:
            logger.error("Failed to store fact: %s", e)
            return False

    def store_agent_name(
        self, user_id: str, name: str, session_id: str
    ) -> bool:
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
            similarity_query=similarity_query
        )
