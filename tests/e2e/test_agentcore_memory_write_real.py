"""Real (non-mocked) AgentCore Memory write/read test.

This test is intentionally *not* deterministic/offline:
- It performs real AWS calls to Bedrock AgentCore Memory via bedrock_agentcore.
- It may incur API costs.

It is skipped unless explicitly enabled.

Enable with:
    MORDECAI_RUN_REAL_TESTS=1

Backwards compatibility:
    MORDECAI_RUN_E2E_AWS=1

What it validates:
- MemoryService.store_fact writes to AgentCore successfully.
- MemoryService.search_memory can find the newly written record.

We use a unique actor_id + unique token in the fact to avoid cross-test
interference and to make cleanup optional.
"""

from __future__ import annotations

import time
import uuid

import pytest

from app.config import AgentConfig
from app.services.memory_service import MemoryService
from tests.e2e.aws_preflight import (
    aws_env_summary,
    mask_access_key_id,
    require_real_tests_enabled,
    skip_if_aws_auth_invalid,
)


@pytest.mark.e2e
@pytest.mark.slow
def test_agentcore_memory_store_and_search_roundtrip_real_aws():
    require_real_tests_enabled(allow_legacy_aws_flag=True)

    # Load real creds/config (secrets.yml / env). This test is expected to fail
    # if credentials are missing or expired.
    cfg = AgentConfig.from_json_file(config_path="config.json", secrets_path="secrets.yml")

    print(
        "[e2e] AWS credential debug (values redacted): "
        f"cfg.aws_region={cfg.aws_region!r} "
        f"cfg.aws_access_key_id={mask_access_key_id(cfg.aws_access_key_id)} "
        f"cfg.aws_secret_access_key={'<set>' if cfg.aws_secret_access_key else '<unset>'} "
        f"cfg.aws_session_token={'<set>' if getattr(cfg, 'aws_session_token', None) else '<unset>'} "
        f"env={aws_env_summary()}"
    )

    # Ensure memory is enabled for this test.
    cfg.memory_enabled = True

    service = MemoryService(cfg)

    print(f"[e2e] AWS env after MemoryService normalization: env={aws_env_summary()}")

    # Validate credentials early (avoid noisy AgentCore errors).
    # We intentionally use the default credential chain here because MemoryService
    # normalizes the AWS_* env vars to match cfg (and clears stale session tokens).
    skip_if_aws_auth_invalid(region_name=cfg.aws_region)

    token = f"e2e-memory-{uuid.uuid4()}"
    actor_id = f"e2e_{uuid.uuid4()}"  # unique namespace
    session_id = f"e2e_{uuid.uuid4()}"

    # Use a natural-language fact so the semanticMemoryStrategy can reliably
    # extract and retrieve it via semantic search.
    fact = f"My favorite food is {token}."

    ok = service.store_fact(
        user_id=actor_id,
        fact=fact,
        session_id=session_id,
        replace_similar=False,
        similarity_query=None,
        write_to_short_term=False,
    )
    assert ok is True, "store_fact returned False; expected a successful AgentCore write"

    # AgentCore indexing/search can be eventually consistent; poll for visibility.
    deadline = time.time() + 180
    last = None
    while time.time() < deadline:
        # Query by meaning, not by the random token (embeddings won't match random
        # identifiers well).
        last = service.search_memory(user_id=actor_id, query="favorite food", memory_type="facts")
        facts = last.get("facts", []) if isinstance(last, dict) else []
        if any(token in str(x) for x in facts):
            return
        time.sleep(5)

    raise AssertionError(
        "Wrote a fact to AgentCore but could not find it via search within 180s. "
        f"Last search result: {last}"
    )
