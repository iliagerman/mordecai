"""Real (opt-in) e2e test for Obsidian STM session handoff.

What it validates:
- On session reset (/new), Mordecai summarizes the previous session and writes it to
  Obsidian (STM + per-session note) *before* clearing in-memory context.
- The next session's system prompt includes the STM content so the model has immediate
  access, even if AgentCore retrieval is eventually consistent.
- After the handoff, the on-disk STM file is cleared (we keep an in-memory cached copy
  for prompt injection).

Run via:
  just run-real-tests
"""

from __future__ import annotations

import uuid

import pytest

from app.config import AgentConfig
from app.services.agent_service import AgentService
from app.services.memory_extraction_service import MemoryExtractionService
from app.services.memory_service import MemoryService
from app.tools.conversation_summary_vault import conversation_summary_path
from app.tools.short_term_memory_vault import short_term_memory_path
from tests.e2e.aws_preflight import (
    require_real_tests_enabled,
    skip_if_aws_auth_invalid,
)


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.asyncio
async def test_obsidian_stm_handoff_on_new_session(tmp_path):
    require_real_tests_enabled(allow_legacy_aws_flag=True)

    print("\n[e2e] === Test: Obsidian STM handoff on /new ===")

    base = AgentConfig.from_json_file(config_path="config.json", secrets_path="secrets.yml")

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

    # Validate credentials early (avoid noisy AgentCore errors). Prefer configured
    # creds when available, to avoid stale env-session-token issues.
    skip_if_aws_auth_invalid(
        region_name=cfg.aws_region,
        aws_access_key_id=cfg.aws_access_key_id,
        aws_secret_access_key=cfg.aws_secret_access_key,
        aws_session_token=getattr(cfg, "aws_session_token", None),
    )

    memory_service = MemoryService(cfg)
    extraction_service = MemoryExtractionService(cfg, memory_service=memory_service)
    agent_service = AgentService(
        cfg,
        memory_service=memory_service,
        extraction_service=extraction_service,
    )

    user_id = f"e2e_stm_{uuid.uuid4()}"

    print(
        "[e2e] Setup: isolated vault + user\n"
        f"      user_id={user_id}\n"
        f"      vault_root={cfg.obsidian_vault_root}"
    )

    # Create a session and at least 2 messages so summarize_and_store runs.
    print("[e2e] Step 1/4: Create a session and send at least one message")
    await agent_service.process_message(user_id, "Let's start. Please reply 'OK'.")

    old_session_id = agent_service._get_session_id(user_id)

    print(f"[e2e] Observed old_session_id={old_session_id}")

    # Trigger /new behavior.
    print("[e2e] Step 2/4: Trigger /new (AgentService.new_session)")
    await agent_service.new_session(user_id)

    assert cfg.obsidian_vault_root is not None

    print("[e2e] Step 3/4: Assert per-session note exists")

    # Per-session conversation summary note should exist.
    note_path = conversation_summary_path(cfg.obsidian_vault_root, user_id, old_session_id)
    assert note_path.exists(), f"Expected session note at {note_path}"

    print(f"[e2e] ✅ Session note exists: {note_path}")

    # STM should be cleared on disk after handoff.
    print("[e2e] Step 4/4: Assert STM is cleared on disk but injected via prompt cache")
    stm_path = short_term_memory_path(cfg.obsidian_vault_root, user_id)
    assert not stm_path.exists(), f"Expected STM to be cleared after handoff: {stm_path}"

    # The new session prompt must include the previous session's summary block.
    prompt = agent_service._build_system_prompt(user_id)
    assert "Short-Term Memory (Obsidian)" in prompt
    assert f"Session summary: {old_session_id}" in prompt

    print("[e2e] ✅ Prompt contains STM handoff summary block")
