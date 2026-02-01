"""Unit tests for onboarding behavior in Telegram handlers.

Regression test: onboarding message must not repeat.

The onboarding flow is triggered in the Telegram handler before messages are
processed by the SQS worker. Historically, the worker created the User row.
If we don't ensure the user exists during onboarding, the handler cannot mark
onboarding as completed and the onboarding message repeats on subsequent
messages.
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
    def __init__(self) -> None:
        super().__init__(vault_root=None)

    def is_enabled(self) -> bool:
        return False

    async def ensure_user_personality_files(self, user_id: str) -> tuple[bool, str]:
        # Not expected to be called in these tests.
        return True, "noop"

    def get_onboarding_message(self, user_id: str) -> str:
        return f"ONBOARD:{user_id}"


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
async def test_onboarding_message_sent_only_once_per_user(user_dao: UserDAO) -> None:
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
        onboarding_service=_StubOnboardingService(),
    )

    # Capture outbound onboarding messages.
    handler._send_response = AsyncMock()  # type: ignore[method-assign]

    user_id = "splintermaster"
    chat_id = 123456

    # First call should send onboarding and mark completion.
    await handler._check_and_handle_onboarding(user_id, chat_id)

    # Second call should no-op (already completed).
    await handler._check_and_handle_onboarding(user_id, chat_id)

    assert handler._send_response.await_count == 1

    assert handler._send_response.call_args is not None
    (sent_chat_id, sent_text) = handler._send_response.call_args.args
    assert sent_chat_id == chat_id
    assert sent_text.startswith("ONBOARD:")

    assert await user_dao.is_onboarding_completed(user_id) is True
