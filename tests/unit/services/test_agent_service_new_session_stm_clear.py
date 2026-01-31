from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import AgentConfig
from app.services.agent_service import AgentService
from app.tools.short_term_memory_vault import short_term_memory_path


@pytest.mark.asyncio
async def test_new_session_clears_obsidian_stm_even_if_read_fails(tmp_path: Path, monkeypatch):
    """Regression test for E2E handoff: stm.md must be deleted after /new.

    We simulate a failure in read_raw_text (e.g. path/IO/encoding edge case) and
    ensure the service still clears stm.md on disk.
    """

    vault = tmp_path / "vault"

    cfg = MagicMock(spec=AgentConfig)
    cfg.skills_base_dir = str(tmp_path / "skills")
    cfg.shared_skills_dir = str(tmp_path / "shared_skills")
    cfg.secrets_path = str(tmp_path / "secrets.yml")
    cfg.obsidian_vault_root = str(vault)
    cfg.personality_max_chars = 20_000
    cfg.personality_enabled = False
    cfg.timezone = "UTC"
    cfg.memory_enabled = False
    cfg.agent_commands = []
    cfg.working_folder_base_dir = str(tmp_path / "workspaces")
    cfg.extraction_timeout_seconds = 1

    extraction_service = SimpleNamespace(
        summarize_and_store=AsyncMock(return_value="Did X"),
    )

    svc = AgentService(config=cfg, memory_service=None, extraction_service=extraction_service)

    # Seed minimal conversation state so new_session runs the summarization path.
    user_id = "u1"
    svc._user_sessions[user_id] = "s1"
    svc._user_message_counts[user_id] = 2
    svc._conversation_history[user_id] = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]

    # Avoid creating real model/agent.
    svc._create_agent = MagicMock(return_value=MagicMock())

    # Force read_raw_text to raise so we verify deletion is attempted regardless.
    import app.tools.short_term_memory_vault as stm_module

    monkeypatch.setattr(
        stm_module, "read_raw_text", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    stm_path = short_term_memory_path(str(vault), user_id)
    stm_path.parent.mkdir(parents=True, exist_ok=True)
    stm_path.write_text("# STM\n\n- something\n", encoding="utf-8")
    assert stm_path.exists()

    await svc.new_session(user_id)

    assert not stm_path.exists(), "Expected stm.md to be deleted after /new"
