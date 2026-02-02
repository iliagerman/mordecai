"""Regression test: late cron wiring should update AgentService internals.

Historically, Application.setup() constructed AgentService before CronService
existed, then later did:

    agent_service.cron_service = cron_service

That *used to* update only the public attribute, leaving:
- prompt builder (has_cron) stale => prompt omitted scheduling instructions
- agent creator cron_service stale => cron tools not registered on new agents

This test enforces that assigning cron_service rewires internals.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.config import AgentConfig
from app.services.agent_service import AgentService


def test_agent_service_cron_service_assignment_rewires_internals() -> None:
    cfg = AgentConfig(
        telegram_bot_token="test-token",
        memory_enabled=False,
        pending_skills_preflight_enabled=False,
    )

    agent_service = AgentService(cfg, memory_service=None, cron_service=None)

    assert agent_service.cron_service is None
    assert agent_service._prompt_builder.has_cron is False
    assert agent_service._agent_creator.cron_service is None

    cron_service = MagicMock()

    # This is the crux: late assignment must update internals too.
    agent_service.cron_service = cron_service

    assert agent_service.cron_service is cron_service
    assert agent_service._prompt_builder.has_cron is True
    assert agent_service._agent_creator.cron_service is cron_service
