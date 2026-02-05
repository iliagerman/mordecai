from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import AgentConfig
from app.services.agent_service import AgentService
from app.tools.short_term_memory_vault import append_memory, short_term_memory_path


def _make_minimal_config(tmp_path):
    cfg = MagicMock(spec=AgentConfig)
    cfg.skills_base_dir = str(tmp_path / "skills")
    cfg.shared_skills_dir = str(tmp_path / "shared_skills")
    cfg.obsidian_vault_root = str(tmp_path / "vault")
    cfg.personality_max_chars = 20_000
    cfg.personality_enabled = False
    cfg.telegram_bot_token = "test-token"
    cfg.timezone = "UTC"
    cfg.memory_enabled = False
    cfg.agent_commands = []
    cfg.working_folder_base_dir = str(tmp_path / "workspaces")
    cfg.extraction_timeout_seconds = 1
    cfg.model_provider = "bedrock"
    cfg.bedrock_model_id = "anthropic.claude-3-sonnet-20240229-v1:0"
    cfg.aws_region = "us-east-1"
    cfg.conversation_window_size = 20
    cfg.user_skills_dir_template = None
    cfg.secrets_path = str(tmp_path / "secrets.yml")
    return cfg


@pytest.mark.asyncio
async def test_consolidate_stm_clears_file_on_success(tmp_path):
    cfg = _make_minimal_config(tmp_path)

    memory_service = MagicMock()

    extraction_service = MagicMock()
    extraction_service.extract_and_store = AsyncMock(return_value=SimpleNamespace(success=True))
    extraction_service.summarize_and_store = AsyncMock(return_value="- Summary")

    svc = AgentService(
        config=cfg,
        memory_service=memory_service,
        extraction_service=extraction_service,
    )

    append_memory(cfg.obsidian_vault_root, "u1", kind="fact", text="User likes cats")
    stm_path = short_term_memory_path(cfg.obsidian_vault_root, "u1")
    assert stm_path.exists()

    await svc.consolidate_short_term_memories_daily()

    # Promoted => STM cleared
    assert not stm_path.exists()

    extraction_service.extract_and_store.assert_awaited_once()
    call_kwargs = extraction_service.extract_and_store.call_args.kwargs
    assert call_kwargs["user_id"] == "u1"
    assert call_kwargs["session_id"].startswith("stm_daily_")
    conv = call_kwargs["conversation_history"]
    assert len(conv) == 2
    assert "User likes cats" in conv[0]["content"]

    extraction_service.summarize_and_store.assert_awaited_once()


@pytest.mark.asyncio
async def test_consolidate_stm_keeps_file_on_failure(tmp_path):
    cfg = _make_minimal_config(tmp_path)

    memory_service = MagicMock()

    extraction_service = MagicMock()
    extraction_service.extract_and_store = AsyncMock(return_value=SimpleNamespace(success=False))
    extraction_service.summarize_and_store = AsyncMock(return_value=None)

    svc = AgentService(
        config=cfg,
        memory_service=memory_service,
        extraction_service=extraction_service,
    )

    append_memory(cfg.obsidian_vault_root, "u1", kind="fact", text="User likes turtles")
    stm_path = short_term_memory_path(cfg.obsidian_vault_root, "u1")
    assert stm_path.exists()

    await svc.consolidate_short_term_memories_daily()

    # Not promoted => STM kept for retry
    assert stm_path.exists()

    extraction_service.extract_and_store.assert_awaited_once()
