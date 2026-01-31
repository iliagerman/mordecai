"""Real (non-mocked) LLM + AgentCore memory end-to-end test.

This suite is intentionally opt-in:
- Uses a real model provider (Bedrock/OpenAI/etc) configured in config.json+secrets.yml.
- Uses real AgentCore Memory to persist and retrieve facts.
- Makes real network calls and may incur costs.

Run via:
  just run-real-tests

What it validates:
- MemoryService.store_fact writes a unique fact into AgentCore under a unique actor_id.
- AgentService.process_message uses MemoryService.retrieve_memory_context (AgentCore read)
  and a real LLM to answer a question that requires the stored fact.

We avoid touching your actual Obsidian vault by using a temp vault root.
"""

from __future__ import annotations

import time
import uuid

import pytest

from app.config import AgentConfig
from app.services.agent_service import AgentService
from app.services.memory_service import MemoryService
from tests.e2e.aws_preflight import (
    aws_env_summary,
    mask_access_key_id,
    require_real_tests_enabled,
    skip_if_aws_auth_invalid,
)


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.asyncio
async def test_real_llm_can_answer_using_agentcore_memory_context(tmp_path):
    require_real_tests_enabled()

    base = AgentConfig.from_json_file(config_path="config.json", secrets_path="secrets.yml")

    print(
        "[e2e] AWS credential debug (values redacted): "
        f"cfg.aws_region={base.aws_region!r} "
        f"cfg.aws_access_key_id={mask_access_key_id(base.aws_access_key_id)} "
        f"cfg.aws_secret_access_key={'<set>' if base.aws_secret_access_key else '<unset>'} "
        f"cfg.aws_session_token={'<set>' if getattr(base, 'aws_session_token', None) else '<unset>'} "
        f"env={aws_env_summary()}"
    )

    # Use isolated on-disk folders for the test.
    cfg = base.model_copy(
        update={
            "session_storage_dir": str(tmp_path / "sessions"),
            "skills_base_dir": str(tmp_path / "skills"),
            "working_folder_base_dir": str(tmp_path / "work"),
            "temp_files_base_dir": str(tmp_path / "tmp"),
            "obsidian_vault_root": str(tmp_path / "vault"),
            "memory_enabled": True,
        }
    )

    memory_service = MemoryService(cfg)

    print(f"[e2e] AWS env after MemoryService normalization: env={aws_env_summary()}")

    # Validate credentials early (avoid noisy AgentCore errors).
    # We intentionally use the default credential chain here because MemoryService
    # normalizes the AWS_* env vars to match cfg (and clears stale session tokens).
    skip_if_aws_auth_invalid(region_name=cfg.aws_region)
    agent_service = AgentService(cfg, memory_service=memory_service)

    actor_id = f"e2e_llm_{uuid.uuid4()}"
    session_id = f"e2e_llm_{uuid.uuid4()}"
    token = f"tok_{uuid.uuid4()}"

    # Store a fact that the model cannot guess.
    fact = f"My secret_token is {token}."

    ok = memory_service.store_fact(
        user_id=actor_id,
        fact=fact,
        session_id=session_id,
        replace_similar=False,
        similarity_query=None,
        write_to_short_term=False,
    )
    assert ok is True

    # Wait for semantic extraction/indexing so retrieval actually returns the stored fact.
    deadline = time.time() + 180
    last = None
    while time.time() < deadline:
        last = memory_service.search_memory(
            user_id=actor_id, query="secret_token", memory_type="facts"
        )
        facts = last.get("facts", []) if isinstance(last, dict) else []
        if any(token in str(x) for x in facts):
            break
        time.sleep(5)
    else:
        raise AssertionError(
            "Stored a fact in AgentCore but it was not retrievable within 180s. "
            f"Last search result: {last}"
        )

    # Ask a question that requires that memory.
    question = (
        "You have a long-term memory. "
        "What is my secret_token? "
        "Reply with just the token value."
        " (If you don't know, say 'UNKNOWN'.)"
    )

    response = await agent_service.process_message(actor_id, question)

    # The model might include extra text; assert the unique token appears.
    assert token in response, (
        "Expected the real LLM to use AgentCore memory context to answer with the stored token. "
        f"Response was: {response!r}"
    )
