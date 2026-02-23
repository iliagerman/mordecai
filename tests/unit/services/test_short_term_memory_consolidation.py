"""Unit tests for daily short-term memory consolidation.

These tests validate the internal (system) daily job that:
- reads Obsidian short-term memory files under me/<USER_ID>/stm.md
- promotes important info into long-term memory via the extraction service
- deletes the short-term file on success

The cron schedule itself is registered in application setup and is not
DB-backed (not user-editable).
"""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import AgentConfig
from app.enums import ModelProvider
from app.services.agent_service import AgentService


@pytest.fixture
def temp_workspace_dir():
    tmp = tempfile.mkdtemp()
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def config(temp_workspace_dir):
    return AgentConfig(
        model_provider=ModelProvider.BEDROCK,
        bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
        telegram_bot_token="test-token",
        session_storage_dir=temp_workspace_dir,
        skills_base_dir=temp_workspace_dir,
        memory_enabled=True,
        working_folder_base_dir=temp_workspace_dir,
    )


@pytest.mark.asyncio
async def test_consolidation_deletes_file_on_success(config, temp_workspace_dir):
    user_id = "u1"

    mock_memory_service = MagicMock()

    mock_extraction_service = MagicMock()
    mock_extraction_service.extract_and_store = AsyncMock(
        return_value=MagicMock(success=True, preferences=[], facts=[], commitments=[])
    )
    mock_extraction_service.summarize_and_store = AsyncMock(return_value="- item")

    service = AgentService(
        config,
        memory_service=mock_memory_service,
        extraction_service=mock_extraction_service,
    )

    # Create STM file in the user's scratchpad inside the workspace
    scratchpad = Path(temp_workspace_dir) / user_id / "scratchpad"
    stm_path = scratchpad / "stm.md"
    stm_path.parent.mkdir(parents=True, exist_ok=True)
    stm_path.write_text("# Short-term memories\n\n- (fact) timezone: UTC\n", encoding="utf-8")

    await service.consolidate_short_term_memories_daily()

    assert stm_path.exists() is False
    mock_extraction_service.extract_and_store.assert_awaited()


@pytest.mark.asyncio
async def test_consolidation_keeps_file_on_failure(config, temp_workspace_dir):
    user_id = "u2"

    mock_memory_service = MagicMock()

    mock_extraction_service = MagicMock()
    mock_extraction_service.extract_and_store = AsyncMock(
        return_value=MagicMock(success=False, error="boom")
    )
    mock_extraction_service.summarize_and_store = AsyncMock(return_value=None)

    service = AgentService(
        config,
        memory_service=mock_memory_service,
        extraction_service=mock_extraction_service,
    )

    # Create STM file in the user's scratchpad inside the workspace
    scratchpad = Path(temp_workspace_dir) / user_id / "scratchpad"
    stm_path = scratchpad / "stm.md"
    stm_path.parent.mkdir(parents=True, exist_ok=True)
    stm_path.write_text("# Short-term memories\n\n- (fact) name: Alice\n", encoding="utf-8")

    await service.consolidate_short_term_memories_daily()

    assert stm_path.exists() is True
    mock_extraction_service.extract_and_store.assert_awaited()
