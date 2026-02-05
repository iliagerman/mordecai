"""Real (opt-in) AgentCore test: replace/overwrite obsolete memories.

Goal:
- Verify that when a user preference/fact changes (e.g., favorite color), we can
  delete old semantically-similar records and store a new one.

Mechanism under test:
- MemoryService.store_fact(..., replace_similar=True, similarity_query=...)
  which uses semantic search to find similar records, deletes them, then writes.

Run via:
  just run-real-tests
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


def _search_with_retry(
    service: MemoryService,
    *,
    actor_id: str,
    query: str,
    deadline_s: int = 180,
    sleep_s: int = 5,
) -> list[str]:
    deadline = time.time() + deadline_s
    last: dict | None = None
    while time.time() < deadline:
        last = service.search_memory(user_id=actor_id, query=query, memory_type="facts")
        facts = last.get("facts", []) if isinstance(last, dict) else []
        # Normalize to strings
        out = [str(x) for x in facts]
        if out:
            return out
        time.sleep(sleep_s)
    return []


@pytest.mark.e2e
@pytest.mark.slow
def test_agentcore_replace_obsolete_fact_favorite_color_real_aws():
    require_real_tests_enabled(allow_legacy_aws_flag=True)

    print("\n[e2e] === Test: Replace obsolete memory (favorite color) ===")

    cfg = AgentConfig.from_json_file(config_path="config.json", secrets_path="secrets.yml")
    cfg.memory_enabled = True

    print(
        "[e2e] AWS credential debug (values redacted): "
        f"cfg.aws_region={cfg.aws_region!r} "
        f"cfg.aws_access_key_id={mask_access_key_id(cfg.aws_access_key_id)} "
        f"cfg.aws_secret_access_key={'<set>' if cfg.aws_secret_access_key else '<unset>'} "
        f"cfg.aws_session_token={'<set>' if getattr(cfg, 'aws_session_token', None) else '<unset>'} "
        f"env={aws_env_summary()}"
    )

    # Normalize env (and avoid mixed credential sources) before STS preflight.
    service = MemoryService(cfg)

    print(f"[e2e] AWS env after MemoryService normalization: env={aws_env_summary()}")

    # Validate credentials early (avoid noisy AgentCore errors). Use the same
    # explicit creds that MemoryService is configured to use.
    skip_if_aws_auth_invalid(
        region_name=cfg.aws_region,
        aws_access_key_id=cfg.aws_access_key_id,
        aws_secret_access_key=cfg.aws_secret_access_key,
        aws_session_token=getattr(cfg, "aws_session_token", None),
    )

    actor_id = f"e2e_replace_{uuid.uuid4()}"
    s1 = f"e2e_replace_{uuid.uuid4()}"
    s2 = f"e2e_replace_{uuid.uuid4()}"

    old_color = "blue"
    new_color = "green"

    # Keep the topic stable so semantic similarity is high.
    old_fact = f"My favorite color is {old_color}."
    new_fact = f"My favorite color is {new_color}."

    # Use a stable similarity query so we search the right semantic neighborhood.
    sim_q = "favorite color"

    print(
        "[e2e] Step 1/4: Write initial fact\n"
        f"      actor_id={actor_id}\n"
        f"      session_id={s1}\n"
        f"      old_fact={old_fact}"
    )

    ok1 = service.store_fact(
        user_id=actor_id,
        fact=old_fact,
        session_id=s1,
        replace_similar=False,
        similarity_query=None,
        write_to_short_term=False,
    )
    assert ok1 is True

    print("[e2e] Step 2/4: Wait until initial fact is searchable")
    facts1 = _search_with_retry(service, actor_id=actor_id, query=sim_q)
    assert any(old_color in f for f in facts1), (
        "Expected initial favorite color to be retrievable before replacing. "
        f"Search results: {facts1}"
    )

    print(
        "[e2e] Step 3/4: Replace with new fact (delete similar then write)\n"
        f"      session_id={s2}\n"
        f"      new_fact={new_fact}"
    )

    ok2 = service.store_fact(
        user_id=actor_id,
        fact=new_fact,
        session_id=s2,
        replace_similar=True,
        similarity_query=sim_q,
        write_to_short_term=False,
    )
    assert ok2 is True

    print("[e2e] Step 4/4: Poll until new fact is searchable and old fact is gone")

    deadline = time.time() + 240
    last_facts: list[str] = []
    while time.time() < deadline:
        last_facts = _search_with_retry(service, actor_id=actor_id, query=sim_q, deadline_s=15)

        has_new = any(new_color in f for f in last_facts)
        has_old = any(old_color in f for f in last_facts)

        if has_new and not has_old:
            print("[e2e] ✅ Replacement observed via search (new present, old absent)")
            return

        # If we only see the new fact but also still see the old fact, wait a bit more.
        time.sleep(5)

    raise AssertionError(
        "Expected replaced memory to converge to only the new favorite color. "
        f"Last search results: {last_facts}"
    )
