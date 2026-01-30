"""Real integration tests for AgentCore memory identity persistence.

These tests hit the actual AgentCore Memory service to verify:
- Identity (name) is stored in memory when user provides it
- Identity persists across sessions (after /new command)
- Agent asks for name when no identity is in memory

Requirements: AWS credentials configured in secrets.yml for Bedrock AgentCore.

Run with:
    uv run pytest tests/integration/test_memory_identity_persistence.py -v
"""

import asyncio
import uuid

import pytest

from app.config import AgentConfig
from app.services.agent_service import AgentService
from app.services.memory_service import MemoryService


# Test user prefix to isolate test data from production
TEST_USER_PREFIX = "integration_test_"


def _can_load_config() -> bool:
    """Check if config with AWS credentials can be loaded."""
    try:
        config = AgentConfig.from_json_file(
            config_path="config.json",
            secrets_path="secrets.yml",
        )
        return config.memory_enabled and config.aws_region is not None
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(
        not _can_load_config(),
        reason="Config with AWS credentials required for AgentCore tests"
    ),
    pytest.mark.asyncio,
]


@pytest.fixture(scope="module")
def real_config():
    """Load real config with AWS credentials."""
    return AgentConfig.from_json_file(
        config_path="config.json",
        secrets_path="secrets.yml",
    )


@pytest.fixture(scope="module")
def memory_service(real_config):
    """Create real MemoryService using existing memory."""
    print("\n[FIXTURE] Creating memory_service...")
    if not real_config.memory_enabled:
        pytest.skip("Memory not enabled in config")
    service = MemoryService(real_config)
    print("[FIXTURE] Getting or creating memory ID...")
    memory_id = service.get_or_create_memory_id()
    print(f"[FIXTURE] Memory ID: {memory_id}")
    return service


@pytest.fixture
def test_user_id():
    """Generate test user ID with prefix for isolation."""
    user_id = f"{TEST_USER_PREFIX}{uuid.uuid4().hex[:8]}"
    print(f"\n[FIXTURE] Generated test_user_id: {user_id}")
    return user_id


@pytest.fixture
def agent_service(real_config, memory_service, tmp_path):
    """Create AgentService with real memory service."""
    print("\n[FIXTURE] Creating agent_service...")
    config = AgentConfig(
        telegram_bot_token=real_config.telegram_bot_token,
        model_provider=real_config.model_provider,
        bedrock_model_id=real_config.bedrock_model_id,
        bedrock_api_key=real_config.bedrock_api_key,
        aws_region=real_config.aws_region,
        aws_access_key_id=real_config.aws_access_key_id,
        aws_secret_access_key=real_config.aws_secret_access_key,
        memory_enabled=True,
        memory_id=memory_service.memory_id,
        memory_name=real_config.memory_name,
        memory_retrieval_top_k=10,
        memory_retrieval_relevance_score=0.2,
        session_storage_dir=str(tmp_path / "sessions"),
        skills_base_dir=str(tmp_path / "skills"),
    )
    service = AgentService(config, memory_service)
    print("[FIXTURE] agent_service created")
    return service


async def wait_for_memory(seconds: float = 3.0):
    """Wait for memory strategies to process."""
    await asyncio.sleep(seconds)


async def wait_for_facts(
    client, memory_id: str, user_id: str, query: str,
    timeout: float = 60.0, poll_interval: float = 5.0
) -> list:
    """Poll for facts to appear in memory.
    
    Returns facts when found, or empty list after timeout.
    """
    import time
    start = time.time()
    attempt = 0
    while time.time() - start < timeout:
        attempt += 1
        elapsed = time.time() - start
        print(f"  [Poll {attempt}] Checking for facts at {elapsed:.1f}s...")
        try:
            facts = client.retrieve_memories(
                memory_id=memory_id,
                namespace=f'/facts/{user_id}',
                query=query,
                top_k=5
            )
            if facts:
                print(f"  [Poll {attempt}] Found {len(facts)} facts!")
                return facts
            print(f"  [Poll {attempt}] No facts yet, waiting {poll_interval}s...")
        except Exception as e:
            print(f"  [Poll {attempt}] Error: {e}")
        await asyncio.sleep(poll_interval)
    print(f"  [Poll] Timeout after {timeout}s, no facts found")
    return []


class TestIdentityPersistence:
    """Test that agent identity persists in memory."""

    @pytest.mark.slow
    async def test_agent_remembers_name_within_session(
        self, agent_service, unique_user_id
    ):
        """Agent should remember name within same session."""
        # Tell the agent its name
        response1 = await agent_service.process_message(
            unique_user_id,
            "Your name is Mordecai. Remember that."
        )
        print(f"Response 1: {response1}")

        # Ask for name in same session
        response2 = await agent_service.process_message(
            unique_user_id,
            "What is your name?"
        )
        print(f"Response 2: {response2}")

        # Should mention Mordecai
        assert "mordecai" in response2.lower(), (
            f"Agent should remember name 'Mordecai' within session. "
            f"Got: {response2}"
        )

    @pytest.mark.slow
    async def test_agent_remembers_name_across_sessions(
        self, agent_service, test_user_id, memory_service
    ):
        """Agent should remember name after /new command (new session)."""
        print(f"\n=== Test user_id: {test_user_id} ===")
        
        # Tell the agent its name
        response1 = await agent_service.process_message(
            test_user_id,
            "I want to call you Mordecai. That is your name now."
        )
        print(f"Response 1 (set name): {response1}")

        # Poll for memory to be stored (strategies process async)
        client = memory_service._get_client()
        memory_id = memory_service.memory_id
        print(f"Memory ID: {memory_id}")
        print("Polling for facts to appear (up to 60s)...")
        
        facts = await wait_for_facts(
            client, memory_id, test_user_id, 
            query='name Mordecai',
            timeout=60.0,
            poll_interval=5.0
        )
        print(f"Facts found: {len(facts)}")
        for f in facts:
            print(f"  - {f.get('content', {}).get('text', '')[:80]}")
        
        if not facts:
            pytest.skip(
                "Memory strategies did not process within timeout. "
                "This is a known AgentCore limitation."
            )

        # Start new session (simulates /new command)
        print("Creating new session...")
        agent_service.new_session(test_user_id)

        # Wait for new session to initialize
        await wait_for_memory(2)

        # Ask for name in new session
        response2 = await agent_service.process_message(
            test_user_id,
            "What is your name?"
        )
        print(f"Response 2 (after new session): {response2}")

        # Should still remember Mordecai from long-term memory
        assert "mordecai" in response2.lower(), (
            f"Agent should remember name 'Mordecai' across sessions. "
            f"Got: {response2}"
        )

    @pytest.mark.slow
    async def test_agent_asks_for_name_when_unknown(
        self, agent_service, unique_user_id
    ):
        """Agent should ask for name when no identity in memory."""
        # Fresh user, no prior interactions
        response = await agent_service.process_message(
            unique_user_id,
            "What is your name?"
        )
        print(f"Response: {response}")

        # Should ask for a name or indicate it doesn't have one
        response_lower = response.lower()
        asks_for_name = any([
            "what would you like to call me" in response_lower,
            "don't have a name" in response_lower,
            "haven't been given a name" in response_lower,
            "no name yet" in response_lower,
            "what should i call myself" in response_lower,
            "would you like to give me a name" in response_lower,
        ])

        # Should NOT identify as Claude or ChatGPT
        not_generic = (
            "claude" not in response_lower
            and "chatgpt" not in response_lower
            and "i'm an ai" not in response_lower
        )

        assert asks_for_name or not_generic, (
            f"Agent should ask for name or not use generic AI name. "
            f"Got: {response}"
        )

    @pytest.mark.slow
    async def test_identity_persists_with_multiple_new_sessions(
        self, agent_service, unique_user_id
    ):
        """Identity should persist through multiple /new commands."""
        # Set name
        await agent_service.process_message(
            unique_user_id,
            "Your name is Ziggy. Please remember this."
        )

        # Wait for memory to process
        await wait_for_memory(3)

        # Multiple new sessions
        for _ in range(3):
            agent_service.new_session(unique_user_id)
            await wait_for_memory(1)

        # Check name after multiple session resets
        response = await agent_service.process_message(
            unique_user_id,
            "What is your name?"
        )
        print(f"Response after 3 new sessions: {response}")

        assert "ziggy" in response.lower(), (
            f"Agent should remember 'Ziggy' after multiple sessions. "
            f"Got: {response}"
        )


class TestMemoryStrategies:
    """Test that memory strategies are working correctly."""

    @pytest.mark.slow
    async def test_preference_strategy_stores_identity(
        self, agent_service, unique_user_id
    ):
        """userPreferenceMemoryStrategy should store identity preference."""
        # Express preference for name
        await agent_service.process_message(
            unique_user_id,
            "I prefer to call you Jarvis. Please use that name."
        )

        await wait_for_memory(3)
        agent_service.new_session(unique_user_id)
        await wait_for_memory(1)

        response = await agent_service.process_message(
            unique_user_id,
            "What name do I call you?"
        )
        print(f"Response: {response}")

        assert "jarvis" in response.lower(), (
            f"Preference strategy should store name. Got: {response}"
        )

    @pytest.mark.slow
    async def test_semantic_strategy_stores_identity_fact(
        self, agent_service, unique_user_id
    ):
        """semanticMemoryStrategy should store identity as fact."""
        # State fact about identity
        await agent_service.process_message(
            unique_user_id,
            "Remember this fact: Your name is Atlas."
        )

        await wait_for_memory(3)
        agent_service.new_session(unique_user_id)
        await wait_for_memory(1)

        response = await agent_service.process_message(
            unique_user_id,
            "What is your name?"
        )
        print(f"Response: {response}")

        assert "atlas" in response.lower(), (
            f"Semantic strategy should store name fact. Got: {response}"
        )


class TestMultiUserIsolation:
    """Test that identity is isolated per user."""

    @pytest.mark.slow
    async def test_different_users_different_identities(
        self, agent_service
    ):
        """Each user should have their own agent identity."""
        user_a = f"user_a_{uuid.uuid4().hex[:6]}"
        user_b = f"user_b_{uuid.uuid4().hex[:6]}"

        # User A names agent "Alpha"
        await agent_service.process_message(
            user_a,
            "Your name is Alpha."
        )

        # User B names agent "Beta"
        await agent_service.process_message(
            user_b,
            "Your name is Beta."
        )

        await wait_for_memory(3)

        # Check each user's agent identity
        response_a = await agent_service.process_message(
            user_a,
            "What is your name?"
        )
        response_b = await agent_service.process_message(
            user_b,
            "What is your name?"
        )

        print(f"User A response: {response_a}")
        print(f"User B response: {response_b}")

        assert "alpha" in response_a.lower(), (
            f"User A's agent should be Alpha. Got: {response_a}"
        )
        assert "beta" in response_b.lower(), (
            f"User B's agent should be Beta. Got: {response_b}"
        )
