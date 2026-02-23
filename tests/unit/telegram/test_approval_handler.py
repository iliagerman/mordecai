"""Tests for the Telegram approval handler (inline keyboard flow).

Covers:
1. create_approval_keyboard generates correct button layout and callback data.
2. ApprovalCallbackHandler routes approve/deny callbacks to ApprovalManager.
3. send_approval_prompt sends the message with correct keyboard.
4. Stale/expired approval requests are handled gracefully.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.telegram.approval_handler import (
    APPROVE_PREFIX,
    DENY_PREFIX,
    ApprovalCallbackHandler,
    create_approval_keyboard,
    send_approval_prompt,
)


class TestCreateApprovalKeyboard:
    def test_keyboard_has_two_buttons(self) -> None:
        kb = create_approval_keyboard("test-123")
        # InlineKeyboardMarkup.inline_keyboard is a list of rows
        assert len(kb.inline_keyboard) == 1
        row = kb.inline_keyboard[0]
        assert len(row) == 2

    def test_approve_button_callback_data(self) -> None:
        kb = create_approval_keyboard("abc-456")
        approve_btn = kb.inline_keyboard[0][0]
        assert approve_btn.text == "Approve"
        assert approve_btn.callback_data == f"{APPROVE_PREFIX}abc-456"

    def test_deny_button_callback_data(self) -> None:
        kb = create_approval_keyboard("abc-456")
        deny_btn = kb.inline_keyboard[0][1]
        assert deny_btn.text == "Deny"
        assert deny_btn.callback_data == f"{DENY_PREFIX}abc-456"


class TestApprovalCallbackHandler:
    def _make_update(self, callback_data: str) -> MagicMock:
        """Create a mock Telegram Update with callback query."""
        query = AsyncMock()
        query.data = callback_data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update = MagicMock()
        update.callback_query = query
        return update

    @pytest.mark.asyncio
    async def test_approve_resolves_approval_manager(self) -> None:
        approval_manager = MagicMock()
        approval_manager.resolve.return_value = True

        handler = ApprovalCallbackHandler(approval_manager)
        update = self._make_update(f"{APPROVE_PREFIX}req-001")

        await handler._handle_callback(update, MagicMock())

        approval_manager.resolve.assert_called_once_with("req-001", True)
        update.callback_query.edit_message_text.assert_called_once()
        msg = update.callback_query.edit_message_text.call_args[0][0]
        assert "Approved" in msg

    @pytest.mark.asyncio
    async def test_deny_resolves_approval_manager(self) -> None:
        approval_manager = MagicMock()
        approval_manager.resolve.return_value = True

        handler = ApprovalCallbackHandler(approval_manager)
        update = self._make_update(f"{DENY_PREFIX}req-002")

        await handler._handle_callback(update, MagicMock())

        approval_manager.resolve.assert_called_once_with("req-002", False)
        msg = update.callback_query.edit_message_text.call_args[0][0]
        assert "Denied" in msg

    @pytest.mark.asyncio
    async def test_stale_request_shows_expired_message(self) -> None:
        approval_manager = MagicMock()
        approval_manager.resolve.return_value = False  # Already expired/handled

        handler = ApprovalCallbackHandler(approval_manager)
        update = self._make_update(f"{APPROVE_PREFIX}req-expired")

        await handler._handle_callback(update, MagicMock())

        msg = update.callback_query.edit_message_text.call_args[0][0]
        assert "expired" in msg.lower() or "already handled" in msg.lower()

    @pytest.mark.asyncio
    async def test_null_query_is_ignored(self) -> None:
        approval_manager = MagicMock()
        handler = ApprovalCallbackHandler(approval_manager)

        update = MagicMock()
        update.callback_query = None

        # Should not raise
        await handler._handle_callback(update, MagicMock())
        approval_manager.resolve.assert_not_called()

    @pytest.mark.asyncio
    async def test_unexpected_callback_data_is_ignored(self) -> None:
        approval_manager = MagicMock()
        handler = ApprovalCallbackHandler(approval_manager)

        update = self._make_update("unknown_prefix:123")
        await handler._handle_callback(update, MagicMock())

        approval_manager.resolve.assert_not_called()

    def test_get_handler_returns_callback_query_handler(self) -> None:
        from telegram.ext import CallbackQueryHandler

        approval_manager = MagicMock()
        handler = ApprovalCallbackHandler(approval_manager)
        result = handler.get_handler()
        assert isinstance(result, CallbackQueryHandler)


class TestSendApprovalPrompt:
    @pytest.mark.asyncio
    async def test_sends_message_with_keyboard(self) -> None:
        bot = AsyncMock()
        await send_approval_prompt(bot, chat_id=12345, text="Approve?", approval_id="p-001")

        bot.send_message.assert_called_once()
        call_kwargs = bot.send_message.call_args[1]
        assert call_kwargs["chat_id"] == 12345
        assert call_kwargs["text"] == "Approve?"
        assert call_kwargs["reply_markup"] is not None
        # Verify the keyboard has the correct approval_id
        kb = call_kwargs["reply_markup"]
        assert f"{APPROVE_PREFIX}p-001" in kb.inline_keyboard[0][0].callback_data

    @pytest.mark.asyncio
    async def test_handles_send_failure_gracefully(self) -> None:
        bot = AsyncMock()
        bot.send_message.side_effect = Exception("Network error")

        # Should not raise
        await send_approval_prompt(bot, chat_id=12345, text="Approve?", approval_id="p-002")
