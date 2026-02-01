"""Unit tests for onboarding behavior in Telegram handlers.

Tests the new onboarding flow where:
1. Onboarding context (soul.md, id.md) is returned to the caller
2. No direct message is sent from _check_and_handle_onboarding
3. The context flows through the message queue to the agent
4. The agent generates a personalized welcome message
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
        # Deprecated method - should not be used in new flow
        return f"ONBOARD:{user_id}"

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

    # But user is still marked as completed (onboarding was attempted)
    assert await user_dao.is_onboarding_completed(user_id) is True


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
