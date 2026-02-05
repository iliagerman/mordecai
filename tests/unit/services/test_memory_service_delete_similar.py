"""Unit tests for MemoryService.delete_similar_records.

This validates we can safely delete incorrect/outdated AgentCore memories by query.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_config():
    from app.config import AgentConfig

    cfg = MagicMock(spec=AgentConfig)
    cfg.aws_region = "us-east-1"
    cfg.aws_access_key_id = None
    cfg.aws_secret_access_key = None
    cfg.memory_id = "mem-1"
    cfg.memory_name = "TestMemory"
    cfg.memory_description = "Test"
    cfg.memory_retrieval_top_k = 10
    cfg.memory_retrieval_relevance_score = 0.5
    cfg.obsidian_vault_root = None
    cfg.personality_max_chars = 20_000
    return cfg


def test_delete_similar_records_dry_run_does_not_delete(monkeypatch, mock_config):
    from app.services.memory_service import MemoryService

    svc = MemoryService(mock_config)
    svc._memory_id = "mem-1"

    # Pretend we found two similar records.
    monkeypatch.setattr(
        svc,
        "_find_similar_records",
        lambda user_id, query, similarity_threshold=0.7: [
            {
                "memoryRecordId": "r1",
                "namespace": f"/facts/{user_id}",
                "score": 0.9,
                "text": "bad fact one",
            },
            {
                "memoryRecordId": "r2",
                "namespace": f"/preferences/{user_id}",
                "score": 0.8,
                "text": "bad pref two",
            },
        ],
    )
    delete_spy = MagicMock(return_value=2)
    monkeypatch.setattr(svc, "_delete_records", delete_spy)

    res = svc.delete_similar_records(user_id="u", query="bad", dry_run=True)
    assert res.matched == 2
    assert res.deleted == 0
    delete_spy.assert_not_called()


def test_delete_similar_records_filters_by_type_and_deletes(monkeypatch, mock_config):
    from app.services.memory_service import MemoryService

    svc = MemoryService(mock_config)
    svc._memory_id = "mem-1"

    monkeypatch.setattr(
        svc,
        "_find_similar_records",
        lambda user_id, query, similarity_threshold=0.7: [
            {
                "memoryRecordId": "r1",
                "namespace": f"/facts/{user_id}",
                "score": 0.9,
                "text": "bad fact one",
            },
            {
                "memoryRecordId": "r2",
                "namespace": f"/preferences/{user_id}",
                "score": 0.8,
                "text": "bad pref two",
            },
        ],
    )
    delete_spy = MagicMock(return_value=1)
    monkeypatch.setattr(svc, "_delete_records", delete_spy)

    res = svc.delete_similar_records(user_id="u", query="bad", memory_type="facts", dry_run=False)
    assert res.matched == 1
    assert res.deleted == 1
    delete_spy.assert_called_once()
