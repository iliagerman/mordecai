"""Unit tests for TelegramMessageSender.

These tests focus on avoiding duplicate/overlapping messages when sending
long responses (especially onboarding) where Telegram's 4096-char limit
requires chunking.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.telegram.message_sender import TelegramMessageSender


@pytest.mark.asyncio
async def test_send_response_sends_single_message_when_short(monkeypatch) -> None:
    bot = MagicMock()
    bot.send_message = AsyncMock()

    # Make formatting deterministic and obviously different from raw.
    from app.telegram import response_formatter as rf

    monkeypatch.setattr(
        rf.TelegramResponseFormatter,
        "format_for_html",
        lambda _self, text: f"<b>{text}</b>",
    )

    sender = TelegramMessageSender(bot)
    await sender.send_response(123, "hello")

    assert bot.send_message.await_count == 1
    call = bot.send_message.call_args
    assert call.kwargs["chat_id"] == 123
    assert call.kwargs["text"] == "<b>hello</b>"
    assert "parse_mode" in call.kwargs


@pytest.mark.asyncio
async def test_send_response_chunks_and_falls_back_per_chunk(monkeypatch) -> None:
    bot = MagicMock()

    # First formatted send succeeds, second fails (simulating Telegram parse error),
    # and the fallback plain-text send for that chunk succeeds.
    bot.send_message = AsyncMock(side_effect=[None, Exception("Bad Request"), None])

    # Make formatter add small markup.
    from app.telegram import response_formatter as rf

    monkeypatch.setattr(
        rf.TelegramResponseFormatter,
        "format_for_html",
        lambda _self, text: f"<i>{text}</i>",
    )

    sender = TelegramMessageSender(bot)

    # Force at least two raw chunks given raw_chunk_len=3500.
    response = "a" * 3600

    await sender.send_response(456, response)

    # We expect:
    # - send formatted chunk 1
    # - send formatted chunk 2 (raises)
    # - send plain chunk 2 as fallback
    assert bot.send_message.await_count == 3

    texts = [c.kwargs["text"] for c in bot.send_message.call_args_list]

    # Must not resend the entire original response as a single fallback.
    assert response not in texts

    # Formatted sends should be different from raw and bounded.
    assert texts[0].startswith("<i>") and texts[0].endswith("</i>")
    assert texts[1].startswith("<i>") and texts[1].endswith("</i>")

    # Fallback should be raw plain text for (the) failing chunk.
    assert texts[2] == ("a" * 100)
