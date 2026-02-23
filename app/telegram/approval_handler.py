"""Telegram inline-keyboard approval handler for credential access requests.

Handles [Approve] / [Deny] callback queries triggered by the credential tool's
approval flow. Communicates with the ApprovalManager to resolve pending requests.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

if TYPE_CHECKING:
    from app.services.identity.approval_manager import ApprovalManager

logger = logging.getLogger(__name__)

# Callback data prefixes for inline buttons.
APPROVE_PREFIX = "cred_approve:"
DENY_PREFIX = "cred_deny:"


def create_approval_keyboard(approval_id: str) -> InlineKeyboardMarkup:
    """Create an inline keyboard with Approve / Deny buttons.

    Args:
        approval_id: The approval request ID embedded in the callback data.

    Returns:
        InlineKeyboardMarkup with two buttons.
    """
    keyboard = [
        [
            InlineKeyboardButton(
                "Approve",
                callback_data=f"{APPROVE_PREFIX}{approval_id}",
            ),
            InlineKeyboardButton(
                "Deny",
                callback_data=f"{DENY_PREFIX}{approval_id}",
            ),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


class ApprovalCallbackHandler:
    """Handles Telegram callback queries for credential approval buttons."""

    def __init__(self, approval_manager: ApprovalManager) -> None:
        self._approval_manager = approval_manager

    def get_handler(self) -> CallbackQueryHandler:
        """Return the CallbackQueryHandler to register with the Telegram app.

        Matches callback data starting with 'cred_approve:' or 'cred_deny:'.
        """
        return CallbackQueryHandler(
            self._handle_callback,
            pattern=f"^({APPROVE_PREFIX}|{DENY_PREFIX})",
        )

    async def _handle_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle an approval callback query.

        Args:
            update: Telegram update containing the callback query.
            context: Callback context.
        """
        query = update.callback_query
        if query is None or query.data is None:
            return

        await query.answer()

        data = query.data
        if data.startswith(APPROVE_PREFIX):
            approval_id = data[len(APPROVE_PREFIX):]
            approved = True
        elif data.startswith(DENY_PREFIX):
            approval_id = data[len(DENY_PREFIX):]
            approved = False
        else:
            logger.warning("Unexpected callback data: %s", data)
            return

        resolved = self._approval_manager.resolve(approval_id, approved)

        if resolved:
            status_text = "Approved" if approved else "Denied"
            emoji = "✅" if approved else "❌"
            await query.edit_message_text(
                f"{emoji} Credential access **{status_text}**.",
                parse_mode="Markdown",
            )
            logger.info(
                "Credential approval callback: id=%s approved=%s",
                approval_id,
                approved,
            )
        else:
            await query.edit_message_text(
                "⏰ This approval request has expired or was already handled.",
            )
            logger.warning(
                "Stale credential approval callback: id=%s",
                approval_id,
            )


async def send_approval_prompt(
    bot,
    chat_id: int,
    text: str,
    approval_id: str,
) -> None:
    """Send an approval prompt with inline keyboard to a Telegram chat.

    This is the callback registered with ApprovalManager.set_send_callback().

    Args:
        bot: Telegram Bot instance.
        chat_id: Chat to send the prompt to.
        text: Prompt message text.
        approval_id: Approval request ID for the inline buttons.
    """
    keyboard = create_approval_keyboard(approval_id)
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning(
            "Failed to send approval prompt to chat %s: %s",
            chat_id,
            e,
        )
