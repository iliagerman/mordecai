"""Regression tests for cron scheduling wiring.

Bug:
- Application creates AgentService without CronService, then later assigns
  `agent_service.cron_service = cron_service`.
- AgentService caches internal references at init (prompt builder flag + agent
  creator cron_service). A late assignment does NOT update those.

Impact:
- The agent does not register cron tools (create_cron_task/list/delete)
- The system prompt omits scheduling instructions
- The model responds "I can't run tasks on a timer" and no cron tasks are created

This test ensures wiring is done via AgentService.set_cron_service(), which
updates internals consistently.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.config import AgentConfig
from app.main import Application


class _DummyTelegramBot:
    """Minimal TelegramBotInterface stub for Application.setup tests."""

    def __init__(self, *args, **kwargs) -> None:  # noqa: D401
        pass

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send_response(self, chat_id: int, response: str) -> None:
        return None

    async def send_file(self, chat_id: int, file_path, caption: str | None = None) -> bool:
        return True

    async def send_photo(self, chat_id: int, file_path, caption: str | None = None) -> bool:
        return True


@pytest.mark.asyncio
async def test_application_setup_wires_cron_into_agent_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Avoid talking to AWS/LocalStack.
    monkeypatch.setattr("app.main.boto3.client", lambda *args, **kwargs: MagicMock())
    # Avoid importing/initializing the real Telegram bot.
    monkeypatch.setattr("app.main.TelegramBotInterface", _DummyTelegramBot)

    cfg = AgentConfig(
        telegram_bot_token="test-token",
        memory_enabled=False,
        pending_skills_preflight_enabled=False,
        auto_create_tables=True,
        database_url="sqlite+aiosqlite:///:memory:",
        localstack_endpoint="http://localhost:4566",
    )

    app = Application(cfg)
    await app.setup()

    assert app.agent_service is not None
    assert app.cron_service is not None

    # Public field is wired.
    assert app.agent_service.cron_service is app.cron_service

    # Internals that previously stayed stale are now wired.
    assert app.agent_service._agent_creator.cron_service is app.cron_service
    assert app.agent_service._prompt_builder.has_cron is True
