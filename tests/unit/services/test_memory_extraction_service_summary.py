"""Unit tests for MemoryExtractionService session summaries.

Summaries are generated and stored (best-effort) when /new is hit via
MemoryExtractionService.summarize_and_store.

This file focuses on summary storage behavior.
"""

from unittest.mock import MagicMock

import pytest

from app.config import AgentConfig
from app.enums import ModelProvider


@pytest.fixture
def mock_config():
    """Minimal config required by MemoryExtractionService."""
    cfg = MagicMock(spec=AgentConfig)
    cfg.model_provider = ModelProvider.BEDROCK
    cfg.bedrock_model_id = "anthropic.claude-3-sonnet-20240229-v1:0"
    cfg.aws_region = "us-east-1"
    cfg.openai_api_key = None
    cfg.openai_model_id = "gpt-4o-mini"
    return cfg


@pytest.mark.asyncio
async def test_summarize_and_store_stores_summary_for_conversation(mock_config):
    from app.services.memory_extraction_service import MemoryExtractionService

    memory_service = MagicMock()

    svc = MemoryExtractionService(config=mock_config, memory_service=memory_service)

    # Avoid real model calls
    svc._summarize_conversation = MagicMock(return_value="- User wants X\n- Decision: Y")

    # Needs at least 2 messages to summarize
    conversation_history = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi!"},
    ]

    summary = await svc.summarize_and_store(
        user_id="u1",
        session_id="s1",
        conversation_history=conversation_history,
    )

    assert summary is not None
    assert "decision" in summary.lower() or "user" in summary.lower()

    memory_service.store_fact.assert_called_once()
    kwargs = memory_service.store_fact.call_args.kwargs
    assert kwargs["user_id"] == "u1"
    assert kwargs["session_id"] == "s1"
    assert kwargs["replace_similar"] is False
    assert "Session summary (s1):" in kwargs["fact"]


@pytest.mark.asyncio
async def test_summarize_and_store_does_not_store_sensitive_summary(mock_config):
    from app.services.memory_extraction_service import MemoryExtractionService

    memory_service = MagicMock()

    svc = MemoryExtractionService(config=mock_config, memory_service=memory_service)

    svc._summarize_conversation = MagicMock(return_value="User api_key=sk-FAKE")

    conversation_history = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi!"},
    ]

    summary = await svc.summarize_and_store(
        user_id="u1",
        session_id="s1",
        conversation_history=conversation_history,
    )

    assert summary is None
    memory_service.store_fact.assert_not_called()
