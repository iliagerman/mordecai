"""Unit tests for onboarding behavior in Telegram handlers.

These tests cover the Telegram-side onboarding trigger logic.

Notes:
- `_check_and_handle_onboarding()` returns onboarding context (soul/id content)
    and is responsible for marking onboarding completed in the DB.
- The actual onboarding *message* is sent from `handle_message()` (so it is
    deterministic and not dependent on an LLM).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from app.dao.user_dao import UserDAO
from app.database import Database
from app.services.onboarding_service import OnboardingService
from app.telegram.message_handlers import TelegramMessageHandlers


class _StubOnboardingService(OnboardingService):
    def __init__(
        self,
        soul_content: str | None = None,
        id_content: str | None = None,
    ) -> None:
        super().__init__(vault_root=None)
        self.soul_content = soul_content
        self.id_content = id_content

    def is_enabled(self) -> bool:
        return True

    async def ensure_user_personality_files(self, user_id: str) -> tuple[bool, str]:
        return True, "Personality files created"

    def get_onboarding_message(self, user_id: str) -> str:
        return f"ONBOARD:{user_id}\n\nSOUL:{self.soul_content}\n\nID:{self.id_content}"

    def get_onboarding_context(self, user_id: str) -> dict[str, str | None] | None:
        """Return onboarding context for agent prompt injection."""
        if self.soul_content is None and self.id_content is None:
            return None
        return {
            "soul": self.soul_content,
            "id": self.id_content,
        }


@pytest_asyncio.fixture
async def test_db() -> AsyncGenerator[Database]:
    db = Database("sqlite+aiosqlite:///:memory:")
    await db.init_db()
    yield db
    await db.close()


@pytest_asyncio.fixture
async def user_dao(test_db: Database) -> UserDAO:
    return UserDAO(test_db)


@pytest.mark.asyncio
async def test_onboarding_context_returned_on_first_interaction(user_dao: UserDAO) -> None:
    """Test that onboarding context is returned on first interaction."""
    logging_service = MagicMock()
    logging_service.log_action = AsyncMock()

    soul_md = """# My Soul

I am helpful and friendly.
I love assisting with tasks.
"""

    id_md = """# My Identity

I am an AI assistant created to help.
"""

    handler = TelegramMessageHandlers(
        config=MagicMock(),
        logging_service=logging_service,
        skill_service=MagicMock(),
        file_service=MagicMock(),
        command_parser=MagicMock(),
        bot_application=MagicMock(),
        get_allowed_users=lambda: set(),
        user_dao=user_dao,
        onboarding_service=_StubOnboardingService(soul_content=soul_md, id_content=id_md),
    )

    user_id = "testuser"
    chat_id = 123456

    # First call should return onboarding context
    context = await handler._check_and_handle_onboarding(user_id, chat_id)

    assert context is not None
    assert context.get("soul") == soul_md
    assert context.get("id") == id_md

    # Verify user is marked as onboarded
    assert await user_dao.is_onboarding_completed(user_id) is True


@pytest.mark.asyncio
async def test_onboarding_returns_none_when_already_completed(user_dao: UserDAO) -> None:
    """Test that onboarding returns None when user already completed onboarding."""
    logging_service = MagicMock()
    logging_service.log_action = AsyncMock()

    handler = TelegramMessageHandlers(
        config=MagicMock(),
        logging_service=logging_service,
        skill_service=MagicMock(),
        file_service=MagicMock(),
        command_parser=MagicMock(),
        bot_application=MagicMock(),
        get_allowed_users=lambda: set(),
        user_dao=user_dao,
        onboarding_service=_StubOnboardingService(
            soul_content="soul content",
            id_content="id content",
        ),
    )

    user_id = "testuser2"
    chat_id = 123456

    # First call - should return context
    context1 = await handler._check_and_handle_onboarding(user_id, chat_id)
    assert context1 is not None

    # Second call - should return None (already completed)
    context2 = await handler._check_and_handle_onboarding(user_id, chat_id)
    assert context2 is None


@pytest.mark.asyncio
async def test_onboarding_returns_none_when_no_content(user_dao: UserDAO) -> None:
    """Test that onboarding returns None when no personality files exist."""
    logging_service = MagicMock()
    logging_service.log_action = AsyncMock()

    # Service with no soul/id content
    handler = TelegramMessageHandlers(
        config=MagicMock(),
        logging_service=logging_service,
        skill_service=MagicMock(),
        file_service=MagicMock(),
        command_parser=MagicMock(),
        bot_application=MagicMock(),
        get_allowed_users=lambda: set(),
        user_dao=user_dao,
        onboarding_service=_StubOnboardingService(soul_content=None, id_content=None),
    )

    user_id = "testuser3"
    chat_id = 123456

    context = await handler._check_and_handle_onboarding(user_id, chat_id)

    # Should return None when no content is available
    assert context is None

    # User should NOT be marked as completed when we couldn't load onboarding content.
    assert await user_dao.is_onboarding_completed(user_id) is False


@pytest.mark.asyncio
async def test_onboarding_returns_none_when_service_disabled(user_dao: UserDAO) -> None:
    """Test that onboarding returns None when onboarding_service is None."""
    logging_service = MagicMock()
    logging_service.log_action = AsyncMock()

    handler = TelegramMessageHandlers(
        config=MagicMock(),
        logging_service=logging_service,
        skill_service=MagicMock(),
        file_service=MagicMock(),
        command_parser=MagicMock(),
        bot_application=MagicMock(),
        get_allowed_users=lambda: set(),
        user_dao=user_dao,
        # onboarding_service=None simulates service not being configured
        onboarding_service=None,
    )

    user_id = "testuser4"
    chat_id = 123456

    context = await handler._check_and_handle_onboarding(user_id, chat_id)

    # Should return None when onboarding_service is None
    assert context is None

    # User should NOT be marked as completed when service is not configured
    assert await user_dao.is_onboarding_completed(user_id) is False


@pytest.mark.asyncio
async def test_onboarding_no_message_sent_directly(user_dao: UserDAO) -> None:
    """Test that _check_and_handle_onboarding does NOT send a message directly.

    The new behavior is to return context for the agent to generate a welcome message.
    """
    logging_service = MagicMock()
    logging_service.log_action = AsyncMock()

    handler = TelegramMessageHandlers(
        config=MagicMock(),
        logging_service=logging_service,
        skill_service=MagicMock(),
        file_service=MagicMock(),
        command_parser=MagicMock(),
        bot_application=MagicMock(),
        get_allowed_users=lambda: set(),
        user_dao=user_dao,
        onboarding_service=_StubOnboardingService(
            soul_content="soul",
            id_content="id",
        ),
    )

    # Stub _send_response to track calls
    handler._send_response = AsyncMock()  # type: ignore[method-assign]

    user_id = "testuser5"
    chat_id = 123456

    await handler._check_and_handle_onboarding(user_id, chat_id)

    # _send_response should NOT be called (no direct message)
    assert handler._send_response.await_count == 0

    # But context should be returned
    assert await user_dao.is_onboarding_completed(user_id) is True


@pytest.mark.asyncio
async def test_handle_message_sends_onboarding_message_first(user_dao: UserDAO) -> None:
    """When onboarding context exists, handle_message should send the onboarding
    message immediately (deterministically) before forwarding the user's message
    for agent processing.
    """

    logging_service = MagicMock()
    logging_service.log_action = AsyncMock()

    command_parser = MagicMock()
    parsed = MagicMock()
    command_parser.parse.return_value = parsed

    handler = TelegramMessageHandlers(
        config=MagicMock(),
        logging_service=logging_service,
        skill_service=MagicMock(),
        file_service=MagicMock(),
        command_parser=command_parser,
        bot_application=MagicMock(),
        get_allowed_users=lambda: set(),
        user_dao=user_dao,
        onboarding_service=_StubOnboardingService(soul_content="SOUL", id_content="ID"),
    )

    # Capture messages sent by the handler.
    sent: list[str] = []

    async def _capture_send(chat_id: int, response: str) -> None:
        sent.append(response)

    handler._send_response = AsyncMock(side_effect=_capture_send)  # type: ignore[method-assign]

    execute_command = AsyncMock()

    # Minimal Update mock needed by handle_message
    update = MagicMock()
    update.effective_chat = MagicMock()
    update.effective_chat.id = 123
    update.effective_user = MagicMock()
    update.effective_user.id = 999
    update.effective_user.username = "testuser"
    update.effective_user.first_name = "Test"
    update.message = MagicMock()
    update.message.text = "hi"

    await handler.handle_message(update, MagicMock(), execute_command)

    # We should have sent an onboarding message.
    assert sent, "Expected an onboarding message to be sent"
    assert "ONBOARD:testuser" in sent[0]
    assert "SOUL:SOUL" in sent[0]
    assert "ID:ID" in sent[0]

    # The user's message should still be forwarded for processing.
    assert execute_command.await_count == 1
