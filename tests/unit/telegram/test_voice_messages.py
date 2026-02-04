"""Unit tests for Telegram voice/audio message handling.

Regression: voice messages should trigger bot processing by being enqueued
as attachments (not silently ignored).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import AgentConfig
from app.enums import ModelProvider
from app.services.file_service import FileMetadata
from app.telegram.bot import TelegramBotInterface
from app.telegram.message_handlers import TelegramMessageHandlers


@pytest.fixture
def config(tmp_path):
    return AgentConfig(
        model_provider=ModelProvider.BEDROCK,
        bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
        telegram_bot_token="test-bot-token",
        session_storage_dir=str(tmp_path),
        skills_base_dir=str(tmp_path),
        temp_files_base_dir=str(tmp_path),
        working_folder_base_dir=str(tmp_path),
        allowed_users=["testuser"],
        enable_file_attachments=True,
    )


@pytest.fixture
def mock_bot_app():
    app = MagicMock()
    bot = MagicMock()
    bot.send_message = AsyncMock()
    app.bot = bot
    return app


@pytest.fixture
def handlers(config, mock_bot_app):
    logging_service = MagicMock()
    logging_service.log_action = AsyncMock()

    skill_service = MagicMock()

    file_service = MagicMock()
    file_service.sanitize_filename.side_effect = lambda s: s
    file_service.download_file = AsyncMock(
        return_value=FileMetadata(
            file_id="file-id",
            file_name="voice_uniq.ogg",
            file_path="/tmp/test/voice_uniq.ogg",
            mime_type="audio/ogg",
            file_size=1234,
            is_image=False,
        )
    )

    return TelegramMessageHandlers(
        config=config,
        logging_service=logging_service,
        skill_service=skill_service,
        file_service=file_service,
        command_parser=MagicMock(),
        bot_application=mock_bot_app,
        get_allowed_users=lambda: config.allowed_users,
    )


@pytest.mark.asyncio
async def test_handle_voice_downloads_and_enqueues(handlers):
    update = MagicMock()
    update.effective_chat.id = 123
    update.effective_user.id = 111
    update.effective_user.username = "testuser"
    update.effective_user.first_name = "Test"

    update.message = MagicMock()
    update.message.voice = MagicMock()
    update.message.voice.file_id = "voice-file-id"
    update.message.voice.file_unique_id = "uniq"
    update.message.voice.file_size = 2048
    update.message.voice.duration = 7
    update.message.voice.mime_type = "audio/ogg"

    # Ensure whitelist passes.
    handlers.reject_if_not_whitelisted = AsyncMock(return_value=False)

    enqueue = AsyncMock()

    await handlers.handle_voice(update, MagicMock(), enqueue_with_attachments=enqueue)

    handlers.file_service.download_file.assert_awaited_once()
    enqueue.assert_awaited_once()

    call_kwargs = enqueue.await_args_list[0].kwargs
    assert call_kwargs["user_id"] == "testuser"
    assert call_kwargs["chat_id"] == 123
    assert "Voice message" in call_kwargs["message"]
    assert len(call_kwargs["attachments"]) == 1

    handlers.logging_service.log_action.assert_awaited()


@pytest.mark.asyncio
async def test_handle_audio_downloads_and_enqueues(handlers):
    update = MagicMock()
    update.effective_chat.id = 123
    update.effective_user.id = 111
    update.effective_user.username = "testuser"
    update.effective_user.first_name = "Test"

    update.message = MagicMock()
    update.message.caption = "please transcribe"
    update.message.audio = MagicMock()
    update.message.audio.file_id = "audio-file-id"
    update.message.audio.file_unique_id = "uniq2"
    update.message.audio.file_size = 4096
    update.message.audio.duration = 12
    update.message.audio.mime_type = "audio/mpeg"
    update.message.audio.file_name = "note.mp3"

    handlers.reject_if_not_whitelisted = AsyncMock(return_value=False)

    enqueue = AsyncMock()

    await handlers.handle_audio(update, MagicMock(), enqueue_with_attachments=enqueue)

    handlers.file_service.download_file.assert_awaited()
    enqueue.assert_awaited_once()

    call_kwargs = enqueue.await_args_list[0].kwargs
    assert call_kwargs["user_id"] == "testuser"
    assert call_kwargs["chat_id"] == 123
    assert call_kwargs["message"] == "please transcribe"
    assert len(call_kwargs["attachments"]) == 1


@pytest.mark.asyncio
async def test_bot_registers_voice_handler(config):
    # Verify _setup_handlers wires VOICE so updates are not ignored.
    with patch("telegram.ext._applicationbuilder.ApplicationBuilder.build") as mock_build:
        mock_app_instance = MagicMock()
        mock_app_instance.bot = MagicMock()
        mock_app_instance.bot.send_message = AsyncMock()
        mock_build.return_value = mock_app_instance

        TelegramBotInterface(
            config=config,
            sqs_client=MagicMock(),
            queue_manager=MagicMock(),
            agent_service=MagicMock(),
            logging_service=MagicMock(),
            skill_service=MagicMock(),
        )

        # Verify that add_handler was called with filters.VOICE
        from telegram.ext import filters

        voice_handler_calls = []
        for call_args in mock_app_instance.add_handler.call_args_list:
            if call_args.args:
                handler = call_args.args[0]
                # Check if this handler uses filters.VOICE
                if hasattr(handler, 'filters') and handler.filters == filters.VOICE:
                    voice_handler_calls.append(call_args)

        assert voice_handler_calls, "VOICE handler was not registered"
