from unittest.mock import MagicMock

import pytest

from app.enums import ModelProvider
from app.services.agent_service import AgentService
from app.services.memory_extraction_service import MemoryExtractionService
from app.tools.short_term_memory_vault import short_term_memory_path


@pytest.mark.asyncio
async def test_new_session_appends_summary_to_stm_and_next_prompt_injects_it(tmp_path, monkeypatch):
    # AgentService config (minimal)
    cfg = MagicMock()
    cfg.skills_base_dir = str(tmp_path / "skills")
    cfg.shared_skills_dir = str(tmp_path / "shared")
    cfg.shared_skills_dir = str(tmp_path / "shared")
    cfg.secrets_path = str(tmp_path / "secrets.yml")
    cfg.obsidian_vault_root = str(tmp_path / "vault")
    cfg.personality_max_chars = 20_000
    cfg.personality_enabled = False
    cfg.timezone = "UTC"
    cfg.memory_enabled = False
    cfg.agent_commands = []
    cfg.working_folder_base_dir = str(tmp_path / "workspaces")
    cfg.extraction_timeout_seconds = 1

    # MemoryExtractionService config (minimal)
    mcfg = MagicMock()
    mcfg.model_provider = ModelProvider.BEDROCK
    mcfg.bedrock_model_id = "anthropic.claude-3-sonnet-20240229-v1:0"
    mcfg.aws_region = "us-east-1"
    mcfg.openai_api_key = None
    mcfg.openai_model_id = "gpt-4o-mini"
    mcfg.obsidian_vault_root = cfg.obsidian_vault_root
    mcfg.personality_max_chars = 20_000

    extraction_service = MemoryExtractionService(config=mcfg, memory_service=None)
    extraction_service._summarize_conversation = MagicMock(return_value="- Did A\n- Decided B")

    svc = AgentService(config=cfg, memory_service=None, extraction_service=extraction_service)

    # Avoid creating a real Strands agent in this unit test.
    monkeypatch.setattr(svc, "_create_agent", lambda _user_id: MagicMock())

    user_id = "u1"

    # Seed some in-memory conversation history + message count so /new will summarize.
    svc._conversation_history[user_id] = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
    ]
    svc._user_message_counts[user_id] = 2

    # Ensure stm doesn't exist yet
    stm_path = short_term_memory_path(cfg.obsidian_vault_root, user_id)
    assert not stm_path.exists()

    _agent, _notif = await svc.new_session(user_id)

    # Summary should be handed off: cached for next-prompt injection, and the
    # on-disk STM scratchpad cleared for the new session.
    assert not stm_path.exists()
    assert "Decided B" in (svc._obsidian_stm_cache.get(user_id) or "")

    # In-memory session state cleared
    assert svc._conversation_history.get(user_id) == []
    assert svc.get_message_count(user_id) == 0

    # Next session prompt includes STM content (injected)
    prompt = svc._build_system_prompt(user_id=user_id)
    assert "Short-Term Memory (Obsidian)" in prompt
    assert "Decided B" in prompt
