"""Integration tests for deterministic explicit-memory writes.

Goal: ensure we are testing *real behavior* (filesystem + parsing + service calls),
not only verifying mocked Agent/Model calls.

These tests focus on AgentService's deterministic fallback that stores explicit
"remember" requests immediately, even if the LLM fails to call a tool.

Source of truth for STM note location:
    <vault>/me/<USER_ID>/stm.md

"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.config import AgentConfig
from app.enums import ModelProvider
from app.services.agent_service import AgentService
from app.services.memory_service import MemoryService


@dataclass
class _FakeGmdpClient:
    def delete_memory_record(self, *args, **kwargs):  # pragma: no cover
        return None


@dataclass
class _FakeMemoryClient:
    """Minimal stub for AgentCore MemoryClient used by MemoryService."""

    gmdp_client: _FakeGmdpClient = field(default_factory=_FakeGmdpClient)

    def create_event(self, *args, **kwargs):
        return None

    def retrieve_memories(self, *args, **kwargs):
        return []


class _FailIfInstantiatedMemoryClient:  # pragma: no cover
    """Guardrail: if this gets instantiated, the test leaked to real AWS."""

    def __init__(self, *args, **kwargs):
        raise AssertionError(
            "Real bedrock_agentcore MemoryClient was instantiated during test (network leak). "
            "Tests must stub MemoryService._get_client/get_or_create_memory_id."
        )


@pytest.mark.asyncio
async def test_process_message_explicit_remember_writes_stm_note(tmp_path, monkeypatch):
    """A misspelled 'remember' request still writes to the STM note.

    This asserts on an actual filesystem side-effect (stm.md creation + content),
    so the test won't pass if we only mock things without exercising the path.
    """

    user_id = "splintermaster"

    config = AgentConfig(
        telegram_bot_token="test-token",
        model_provider=ModelProvider.BEDROCK,
        bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
        session_storage_dir=str(tmp_path / "sessions"),
        skills_base_dir=str(tmp_path / "skills"),
        memory_enabled=True,
        obsidian_vault_root=str(tmp_path / "vault"),
    )

    memory_service = MemoryService(config)

    # Prevent any real AWS calls: if something instantiates MemoryClient, fail.
    monkeypatch.setattr(
        "app.services.memory_service.MemoryClient",
        _FailIfInstantiatedMemoryClient,
        raising=True,
    )
    monkeypatch.setattr(
        "app.services.memory_service.MemoryService.get_or_create_memory_id",
        lambda self: "TestMemory",
        raising=True,
    )
    monkeypatch.setattr(
        "app.services.memory_service.MemoryService._get_client",
        lambda self: _FakeMemoryClient(),
        raising=True,
    )
    monkeypatch.setattr(
        "app.services.memory_service.MemoryService.create_session_manager",
        lambda self, *a, **k: None,
        raising=True,
    )
    monkeypatch.setattr(
        "app.services.memory_service.MemoryService.retrieve_memory_context",
        lambda self, *a, **k: {"facts": [], "preferences": []},
        raising=True,
    )

    # Keep the LLM/Agent side inert; the behavior under test happens before agent(prompt).
    class _FakeAgentResult:
        message = {"content": [{"text": "ok"}]}

    class _FakeAgent:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, prompt: str):
            return _FakeAgentResult()

        @property
        def messages(self):
            return []

    # Patch Agent at the agent_creation module level (where it's imported)
    monkeypatch.setattr("app.services.agent.agent_creation.Agent", _FakeAgent)

    # Patch ModelFactory.create to avoid BedrockModel instantiation
    class _FakeModel:
        async def stream(self, *args, **kwargs):
            # Return an empty async generator to avoid real LLM calls
            return
            yield  # This makes it an async generator

    monkeypatch.setattr("app.services.agent.model_factory.ModelFactory.create", lambda *a, **k: _FakeModel())

    service = AgentService(config, memory_service=memory_service)

    # Real-world typoed message from logs
    msg = "I meed you to remeber my favorite food is piza"

    await service.process_message(user_id, msg)

    stm_path = tmp_path / "vault" / "users" / user_id / "stm.md"
    assert stm_path.exists(), "Expected STM note to be created under <vault>/users/<USER_ID>/stm.md"

    content = stm_path.read_text(encoding="utf-8")
    assert "# STM" in content
    assert "favorite food is piza" in content


@pytest.mark.asyncio
async def test_process_message_does_not_write_stm_for_retrieval_question(tmp_path, monkeypatch):
    """We should not treat 'Remember when ...?' as a storage request."""

    user_id = "splintermaster"

    config = AgentConfig(
        telegram_bot_token="test-token",
        model_provider=ModelProvider.BEDROCK,
        bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
        session_storage_dir=str(tmp_path / "sessions"),
        skills_base_dir=str(tmp_path / "skills"),
        memory_enabled=True,
        obsidian_vault_root=str(tmp_path / "vault"),
    )

    memory_service = MemoryService(config)

    # Prevent any real AWS calls: if something instantiates MemoryClient, fail.
    monkeypatch.setattr(
        "app.services.memory_service.MemoryClient",
        _FailIfInstantiatedMemoryClient,
        raising=True,
    )
    monkeypatch.setattr(
        "app.services.memory_service.MemoryService.get_or_create_memory_id",
        lambda self: "TestMemory",
        raising=True,
    )
    monkeypatch.setattr(
        "app.services.memory_service.MemoryService._get_client",
        lambda self: _FakeMemoryClient(),
        raising=True,
    )
    monkeypatch.setattr(
        "app.services.memory_service.MemoryService.create_session_manager",
        lambda self, *a, **k: None,
        raising=True,
    )
    monkeypatch.setattr(
        "app.services.memory_service.MemoryService.retrieve_memory_context",
        lambda self, *a, **k: {"facts": [], "preferences": []},
        raising=True,
    )

    class _FakeAgentResult:
        message = {"content": [{"text": "ok"}]}

    class _FakeAgent:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, prompt: str):
            return _FakeAgentResult()

        @property
        def messages(self):
            return []

    # Patch Agent at the agent_creation module level (where it's imported)
    monkeypatch.setattr("app.services.agent.agent_creation.Agent", _FakeAgent)

    # Patch ModelFactory.create to avoid BedrockModel instantiation
    class _FakeModel:
        async def stream(self, *args, **kwargs):
            # Return an empty async generator to avoid real LLM calls
            return
            yield  # This makes it an async generator

    monkeypatch.setattr("app.services.agent.model_factory.ModelFactory.create", lambda *a, **k: _FakeModel())

    service = AgentService(config, memory_service=memory_service)

    await service.process_message(user_id, "Remember when we met at that cafe?")

    stm_path = tmp_path / "vault" / "users" / user_id / "stm.md"
    assert not stm_path.exists(), "Retrieval phrasing should not create STM note"
