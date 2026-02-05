"""Unit tests for forget_memory tool."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.models.agent import ForgetMemoryResult, MemoryRecordMatch
from app.tools.forget_memory import forget_memory, set_memory_context


@pytest.fixture(autouse=True)
def reset_globals():
    import app.tools.forget_memory as module

    module._memory_service = None
    module._current_user_id = None
    yield
    module._memory_service = None
    module._current_user_id = None


def test_forget_memory_requires_query():
    assert "No query" in forget_memory(query="")


def test_forget_memory_requires_context():
    assert "Memory service not available" in forget_memory(query="x")


def test_forget_memory_lists_matches_dry_run():
    mock_service = MagicMock()
    mock_service.delete_similar_records.return_value = ForgetMemoryResult(
        user_id="test-user",
        query="himalaya.toml",
        memory_type="facts",
        similarity_threshold=0.7,
        dry_run=True,
        matched=1,
        deleted=0,
        matches=[
            MemoryRecordMatch(
                memory_record_id="rec-1",
                namespace="/facts/test-user",
                score=0.9,
                text_preview="The user's Himalaya config is located at /app/...",
            )
        ],
    )

    set_memory_context(mock_service, "test-user")

    out = forget_memory(query="himalaya.toml", dry_run=True)
    assert "Dry-run" in out
    assert "rec-1" in out
    assert "Himalaya" in out


def test_forget_memory_handles_no_matches():
    mock_service = MagicMock()
    mock_service.delete_similar_records.return_value = ForgetMemoryResult(
        user_id="test-user",
        query="nope",
        memory_type="all",
        similarity_threshold=0.7,
        dry_run=True,
        matched=0,
        deleted=0,
        matches=[],
    )

    set_memory_context(mock_service, "test-user")

    out = forget_memory(query="nope")
    assert "No matching memories" in out
