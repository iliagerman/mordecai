"""Unit tests for Telegram bot.

Tests Telegram bot functionality including:
- Property 24: Message Enqueue to User Queue
- Command execution routing
- Message formatting
- Handler delegation

Requirements: 11.5, 11.6, 11.2, 12.2
"""

import json
import shutil
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import AgentConfig
from app.enums import CommandType, ModelProvider
from app.services.command_parser import CommandParser, ParsedCommand
from app.telegram.bot import TelegramBotInterface
from app.telegram.command_executor import CommandExecutor
from app.telegram.message_handlers import TelegramMessageHandlers
from app.telegram.message_queue import MessageQueueHandler
from app.telegram.response_formatter import TelegramResponseFormatter


class TestTelegramBotEnqueueMessage:
    """Tests for message enqueueing to SQS.

    **Property 24: Message Enqueue to User Queue**
    *For any* message sent via Telegram, it should be enqueued to the
    correct user's SQS queue.
    **Validates: Requirements 11.2, 12.2**
    """

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for session storage."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    @pytest.fixture
    def config(self, temp_dir):
        """Create test configuration."""
        return AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            telegram_bot_token="test-bot-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
        )

    @pytest.fixture
    def mock_sqs_client(self):
        """Create mock SQS client."""
        client = MagicMock()
        client.send_message = MagicMock(return_value={"MessageId": "test-msg-id"})
        return client

    @pytest.fixture
    def mock_queue_manager(self):
        """Create mock queue manager."""
        manager = MagicMock()
        manager.get_or_create_queue = MagicMock(
            return_value="https://sqs.us-east-1.amazonaws.com/123456789/agent-user-test"
        )
        return manager

    @pytest.fixture
    def mock_agent_service(self):
        """Create mock agent service."""
        service = MagicMock()
        # new_session is async and returns (agent, notification)
        service.new_session = AsyncMock(return_value=(MagicMock(), "✨ New session started!"))
        return service

    @pytest.fixture
    def mock_logging_service(self):
        """Create mock logging service."""
        service = MagicMock()
        service.log_action = AsyncMock()
        service.get_recent_logs = AsyncMock(return_value=[])
        return service

    @pytest.fixture
    def mock_skill_service(self):
        """Create mock skill service."""
        return MagicMock()

    @pytest.fixture
    @patch("app.telegram.bot.Application")
    def bot(
        self,
        mock_app,
        config,
        mock_sqs_client,
        mock_queue_manager,
        mock_agent_service,
        mock_logging_service,
        mock_skill_service,
    ):
        """Create TelegramBotInterface instance with mocks."""
        # Setup mock application
        mock_app_instance = MagicMock()
        mock_app_instance.bot = MagicMock()
        mock_app_instance.bot.send_message = AsyncMock()
        mock_app.builder.return_value.token.return_value.build.return_value = mock_app_instance

        return TelegramBotInterface(
            config=config,
            sqs_client=mock_sqs_client,
            queue_manager=mock_queue_manager,
            agent_service=mock_agent_service,
            logging_service=mock_logging_service,
            skill_service=mock_skill_service,
        )

    @pytest.mark.asyncio
    async def test_enqueue_message_sends_to_sqs(self, bot, mock_sqs_client, mock_queue_manager):
        """Test _enqueue_message sends message to SQS queue."""
        user_id = "12345"
        chat_id = 67890
        message = "Hello, agent!"

        await bot._enqueue_message(user_id, chat_id, message)

        # Verify queue was retrieved/created for user
        mock_queue_manager.get_or_create_queue.assert_called_once_with(user_id)

        # Verify message was sent to SQS
        mock_sqs_client.send_message.assert_called_once()
        call_args = mock_sqs_client.send_message.call_args

        assert call_args.kwargs["QueueUrl"] == mock_queue_manager.get_or_create_queue.return_value

        # Verify message body contains correct data
        body = json.loads(call_args.kwargs["MessageBody"])
        assert body["user_id"] == user_id
        assert body["message"] == message
        assert body["chat_id"] == chat_id
        assert "timestamp" in body

    @pytest.mark.asyncio
    async def test_enqueue_message_uses_correct_queue_url(
        self, bot, mock_sqs_client, mock_queue_manager
    ):
        """Test message is sent to the correct user's queue URL."""
        user_id = "user-abc"
        expected_queue_url = "https://sqs.us-east-1.amazonaws.com/123/agent-user-abc"
        mock_queue_manager.get_or_create_queue.return_value = expected_queue_url

        await bot._enqueue_message(user_id, 123, "test message")

        call_args = mock_sqs_client.send_message.call_args
        assert call_args.kwargs["QueueUrl"] == expected_queue_url

    @pytest.mark.asyncio
    async def test_enqueue_message_includes_timestamp(self, bot, mock_sqs_client):
        """Test enqueued message includes ISO timestamp."""
        await bot._enqueue_message("user-1", 123, "test")

        call_args = mock_sqs_client.send_message.call_args
        body = json.loads(call_args.kwargs["MessageBody"])

        # Verify timestamp is valid ISO format
        timestamp = datetime.fromisoformat(body["timestamp"].replace("Z", "+00:00"))
        assert timestamp is not None

    @pytest.mark.asyncio
    async def test_enqueue_message_logs_action(self, bot, mock_logging_service):
        """Test enqueueing message logs the action."""
        await bot._enqueue_message("user-1", 123, "test message")

        mock_logging_service.log_action.assert_called_once()
        call_args = mock_logging_service.log_action.call_args
        assert call_args.kwargs["user_id"] == "user-1"
        assert "enqueued" in call_args.kwargs["action"].lower()


class TestMessageQueueHandler:
    """Tests for MessageQueueHandler module.

    Tests the SQS message enqueuing functionality.
    """

    @pytest.fixture
    def mock_sqs_client(self):
        """Create mock SQS client."""
        client = MagicMock()
        client.send_message = MagicMock(return_value={"MessageId": "test-msg-id"})
        return client

    @pytest.fixture
    def mock_queue_manager(self):
        """Create mock queue manager."""
        manager = MagicMock()
        manager.get_or_create_queue = MagicMock(
            return_value="https://sqs.us-east-1.amazonaws.com/123456789/agent-user-test"
        )
        return manager

    @pytest.fixture
    def queue_handler(self, mock_sqs_client, mock_queue_manager):
        """Create MessageQueueHandler instance."""
        return MessageQueueHandler(mock_sqs_client, mock_queue_manager)

    def test_enqueue_message_sends_to_sqs(self, queue_handler, mock_sqs_client, mock_queue_manager):
        """Test enqueue_message sends message to SQS queue."""
        user_id = "12345"
        chat_id = 67890
        message = "Hello, agent!"

        queue_handler.enqueue_message(user_id, chat_id, message)

        # Verify queue was retrieved/created for user
        mock_queue_manager.get_or_create_queue.assert_called_once_with(user_id)

        # Verify message was sent to SQS
        mock_sqs_client.send_message.assert_called_once()
        call_args = mock_sqs_client.send_message.call_args

        assert call_args.kwargs["QueueUrl"] == mock_queue_manager.get_or_create_queue.return_value

        # Verify message body contains correct data
        body = json.loads(call_args.kwargs["MessageBody"])
        assert body["user_id"] == user_id
        assert body["message"] == message
        assert body["chat_id"] == chat_id
        assert "timestamp" in body

    def test_enqueue_message_with_attachments(self, queue_handler, mock_sqs_client):
        """Test enqueue_message_with_attachments includes attachment data."""
        from app.telegram.models import TelegramAttachment

        user_id = "user-1"
        chat_id = 123
        message = "Check this file"
        attachments = [
            TelegramAttachment(
                file_id="abc123",
                file_name="document.pdf",
                file_path="/path/to/document.pdf",
                mime_type="application/pdf",
                file_size=1024,
                is_image=False,
            )
        ]

        queue_handler.enqueue_message_with_attachments(user_id, chat_id, message, attachments)

        # Verify message was sent to SQS
        mock_sqs_client.send_message.assert_called_once()
        call_args = mock_sqs_client.send_message.call_args
        body = json.loads(call_args.kwargs["MessageBody"])

        # Verify attachments are included
        assert "attachments" in body
        assert len(body["attachments"]) == 1
        assert body["attachments"][0]["file_id"] == "abc123"
        assert body["attachments"][0]["file_name"] == "document.pdf"


class TestCommandExecutor:
    """Tests for CommandExecutor module.

    Tests that commands are correctly routed to handlers.
    **Validates: Requirements 11.5, 11.6**
    """

    @pytest.fixture
    def mock_agent_service(self):
        """Create mock agent service."""
        service = MagicMock()
        service.new_session = AsyncMock(return_value=(MagicMock(), "✨ New session started!"))

        # Long-term memory is accessed via agent_service.memory_service
        memory_service = MagicMock()
        service.memory_service = memory_service
        return service

    @pytest.fixture
    def mock_logging_service(self):
        """Create mock logging service."""
        service = MagicMock()
        service.log_action = AsyncMock()
        service.get_recent_logs = AsyncMock(return_value=[])
        return service

    @pytest.fixture
    def mock_skill_service(self):
        """Create mock skill service."""
        return MagicMock()

    @pytest.fixture
    def command_parser(self):
        """Create command parser."""
        return CommandParser()

    @pytest.fixture
    def executor(
        self, mock_agent_service, mock_logging_service, mock_skill_service, command_parser
    ):
        """Create CommandExecutor instance."""
        enqueue_callback = AsyncMock()
        send_response_callback = AsyncMock()
        return CommandExecutor(
            agent_service=mock_agent_service,
            skill_service=mock_skill_service,
            logging_service=mock_logging_service,
            command_parser=command_parser,
            enqueue_callback=enqueue_callback,
            send_response_callback=send_response_callback,
        )

    @pytest.mark.asyncio
    async def test_execute_command_routes_message_to_enqueue(self, executor):
        """Test MESSAGE command type routes to enqueue callback."""
        parsed = ParsedCommand(CommandType.MESSAGE, ["Hello agent"])

        await executor.execute_command(parsed, "user-1", 123, "Hello agent")

        executor._enqueue_message.assert_called_once_with("user-1", 123, "Hello agent", None)

    @pytest.mark.asyncio
    async def test_execute_command_routes_new_to_handler(self, executor, mock_agent_service):
        """Test NEW command type routes to new session handler."""
        parsed = ParsedCommand(CommandType.NEW)

        await executor.execute_command(parsed, "user-1", 123, "new")

        mock_agent_service.new_session.assert_called_once_with("user-1")

    @pytest.mark.asyncio
    async def test_execute_command_routes_logs_to_handler(self, executor, mock_logging_service):
        """Test LOGS command type routes to logs handler."""
        parsed = ParsedCommand(CommandType.LOGS)

        await executor.execute_command(parsed, "user-1", 123, "logs")

        mock_logging_service.get_recent_logs.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_command_routes_help_to_handler(self, executor):
        """Test HELP command type sends help text."""
        parsed = ParsedCommand(CommandType.HELP)

        await executor.execute_command(parsed, "user-1", 123, "help")

        executor._send_response.assert_called_once()
        # Verify help text contains command info
        help_text = executor._send_response.call_args[0][1]
        assert "new" in help_text.lower()

    @pytest.mark.asyncio
    async def test_execute_command_forget_dry_run_sends_response(
        self, executor, mock_agent_service
    ):
        from app.models.agent import ForgetMemoryResult, MemoryRecordMatch

        mock_agent_service.memory_service.delete_similar_records.return_value = ForgetMemoryResult(
            user_id="user-1",
            query="himalaya",
            memory_type="all",
            similarity_threshold=0.7,
            dry_run=True,
            matched=1,
            deleted=0,
            matches=[
                MemoryRecordMatch(
                    memory_record_id="rec-1",
                    namespace="/facts/user-1",
                    score=0.9,
                    text_preview="Bad himalaya memory",
                )
            ],
        )

        parsed = ParsedCommand(CommandType.FORGET, ["himalaya"])
        await executor.execute_command(parsed, "user-1", 123, "forget himalaya")

        executor._send_response.assert_called_once()
        mock_agent_service.memory_service.delete_similar_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_command_forget_delete_sends_response(self, executor, mock_agent_service):
        from app.models.agent import ForgetMemoryResult, MemoryRecordMatch

        mock_agent_service.memory_service.delete_similar_records.return_value = ForgetMemoryResult(
            user_id="user-1",
            query="himalaya",
            memory_type="all",
            similarity_threshold=0.7,
            dry_run=False,
            matched=1,
            deleted=1,
            matches=[
                MemoryRecordMatch(
                    memory_record_id="rec-1",
                    namespace="/facts/user-1",
                    score=0.9,
                    text_preview="Bad himalaya memory",
                )
            ],
        )

        parsed = ParsedCommand(CommandType.FORGET_DELETE, ["himalaya"])
        await executor.execute_command(parsed, "user-1", 123, "forget! himalaya")

        executor._send_response.assert_called_once()
        mock_agent_service.memory_service.delete_similar_records.assert_called_once()


class TestTelegramMessageHandlers:
    """Tests for TelegramMessageHandlers module.

    Tests the message handlers for Telegram updates.
    """

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    @pytest.fixture
    def config(self, temp_dir):
        """Create test configuration."""
        return AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            telegram_bot_token="test-bot-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
        )

    @pytest.fixture
    def mock_bot_app(self):
        """Create mock bot application."""
        app = MagicMock()
        bot = MagicMock()
        bot.send_message = AsyncMock()
        app.bot = bot
        return app

    @pytest.fixture
    def handlers(self, config, mock_bot_app):
        """Create TelegramMessageHandlers instance."""
        mock_logging_service = MagicMock()
        mock_logging_service.log_action = AsyncMock()
        mock_skill_service = MagicMock()
        mock_file_service = MagicMock()
        mock_command_parser = CommandParser()

        return TelegramMessageHandlers(
            config=config,
            logging_service=mock_logging_service,
            skill_service=mock_skill_service,
            file_service=mock_file_service,
            command_parser=mock_command_parser,
            bot_application=mock_bot_app,
            get_allowed_users=lambda: [],
        )

    def test_extract_telegram_identity(self, handlers):
        """Test extraction of user identity from Telegram update."""
        update = MagicMock()
        update.effective_user.id = 12345
        update.effective_user.username = "testuser"
        update.effective_user.first_name = "Test"

        user_id, telegram_user_id, username, display_name = handlers.extract_telegram_identity(
            update
        )

        assert user_id == "testuser"
        assert telegram_user_id == "12345"
        assert username == "testuser"
        assert display_name == "Test"

    def test_extract_telegram_identity_no_username(self, handlers):
        """Test identity extraction when user has no username."""
        update = MagicMock()
        update.effective_user.id = 12345
        update.effective_user.username = None
        update.effective_user.first_name = "Test"

        user_id, telegram_user_id, username, display_name = handlers.extract_telegram_identity(
            update
        )

        assert user_id is None  # Username is primary identifier
        assert telegram_user_id == "12345"
        assert username is None


class TestTelegramResponseFormatter:
    """Tests for TelegramResponseFormatter module.

    Tests HTML formatting of Telegram messages.
    """

    @pytest.fixture
    def formatter(self):
        """Create TelegramResponseFormatter instance."""
        return TelegramResponseFormatter()

    def test_escapes_html_special_characters(self, formatter):
        """Test that HTML special characters are escaped."""
        text = "Use <script> and & symbols"
        result = formatter.format_for_html(text)

        assert "&lt;script&gt;" in result
        assert "&amp;" in result

    def test_converts_headers_to_bold(self, formatter):
        """Test markdown headers are converted to bold HTML."""
        text = "# Header 1\n## Header 2\n### Header 3"
        result = formatter.format_for_html(text)

        assert "<b>Header 1</b>" in result
        assert "<b>Header 2</b>" in result
        assert "<b>Header 3</b>" in result

    def test_converts_bold_markdown(self, formatter):
        """Test **bold** is converted to <b>bold</b>."""
        text = "This is **bold text** here"
        result = formatter.format_for_html(text)

        assert "<b>bold text</b>" in result

    def test_converts_italic_markdown(self, formatter):
        """Test *italic* is converted to <i>italic</i>."""
        text = "This is *italic text* here"
        result = formatter.format_for_html(text)

        assert "<i>italic text</i>" in result

    def test_converts_inline_code(self, formatter):
        """Test `code` is converted to <code>code</code>."""
        text = "Use `print()` function"
        result = formatter.format_for_html(text)

        assert "<code>print()</code>" in result

    def test_converts_code_blocks(self, formatter):
        """Test ```code blocks``` are converted to <pre>code</pre>."""
        text = "```python\nprint('hello')\n```"
        result = formatter.format_for_html(text)

        assert "<pre>" in result
        assert "</pre>" in result
        assert "print" in result

    def test_converts_links(self, formatter):
        """Test [text](url) is converted to <a href="url">text</a>."""
        text = "Check [Google](https://google.com) for info"
        result = formatter.format_for_html(text)

        assert '<a href="https://google.com">Google</a>' in result

    def test_converts_markdown_table_to_list(self, formatter):
        """Regression test: markdown tables should be converted to list.

        Telegram does not render markdown tables well. We should convert them
        into a human-friendly list format.
        """
        text = """| Time | Sender | Subject |
|------|--------|---------|
| 16:49 | Google | Reminder |
| 17:27 | Apple | Update |"""
        result = formatter.format_for_html(text)

        # Should not preserve pipe-table formatting.
        assert "<pre>" not in result
        assert "|" not in result

        # Should preserve the data in a readable list form.
        assert "1." in result
        assert "2." in result
        assert "Google" in result
        assert "Apple" in result

    def test_plain_text_unchanged(self, formatter):
        """Test plain text without markdown passes through."""
        text = "Just plain text here"
        result = formatter.format_for_html(text)

        assert "Just plain text here" in result

    def test_mixed_formatting(self, formatter):
        """Test text with multiple formatting types."""
        text = "# Title\nThis is **bold** and *italic* with `code`"
        result = formatter.format_for_html(text)

        assert "<b>Title</b>" in result
        assert "<b>bold</b>" in result
        assert "<i>italic</i>" in result
        assert "<code>code</code>" in result


class TestTelegramBotWhitelist:
    """Tests for whitelist functionality via TelegramMessageHandlers."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    @pytest.fixture
    def config(self, temp_dir):
        """Create test configuration."""
        return AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            telegram_bot_token="test-token",
            session_storage_dir=str(temp_dir),
            skills_base_dir=str(temp_dir),
            allowed_users=["someone-else"],
        )

    @pytest.fixture
    def mock_bot_app(self):
        """Create mock bot application."""
        app = MagicMock()
        bot = MagicMock()
        bot.send_message = AsyncMock()
        app.bot = bot
        return app

    @pytest.mark.asyncio
    async def test_reject_if_not_whitelisted_rejects_unknown_user(self, config, mock_bot_app):
        """Test unknown user is rejected."""
        mock_logging_service = MagicMock()
        mock_logging_service.log_action = AsyncMock()

        # Create a whitelist that doesn't include testuser
        allowed_users = ["someone-else"]

        handlers = TelegramMessageHandlers(
            config=config,
            logging_service=mock_logging_service,
            skill_service=MagicMock(),
            file_service=MagicMock(),
            command_parser=CommandParser(),
            bot_application=mock_bot_app,
            get_allowed_users=lambda: allowed_users,
        )

        rejected = await handlers.reject_if_not_whitelisted("111", "testuser", 123)

        assert rejected is True
        mock_logging_service.log_action.assert_called_once()

    @pytest.mark.asyncio
    async def test_username_in_allowlist_allows_user(self, tmp_path, mock_bot_app):
        """Test username in allowlist allows user."""
        config = AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            telegram_bot_token="test-token",
            session_storage_dir=str(tmp_path),
            skills_base_dir=str(tmp_path),
            allowed_users=["testuser"],
        )

        handlers = TelegramMessageHandlers(
            config=config,
            logging_service=MagicMock(),
            skill_service=MagicMock(),
            file_service=MagicMock(),
            command_parser=CommandParser(),
            bot_application=mock_bot_app,
            get_allowed_users=lambda: config.allowed_users,
        )

        rejected = await handlers.reject_if_not_whitelisted("111", "testuser", 123)
        assert rejected is False


class TestTelegramBotIntegration:
    """Tests for integration between bot and handler modules."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    @pytest.fixture
    def config(self, temp_dir):
        """Create test configuration."""
        return AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            telegram_bot_token="test-bot-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
        )

    @pytest.fixture
    @patch("app.telegram.bot.Application")
    def bot(self, mock_app, config):
        """Create TelegramBotInterface instance."""
        mock_app_instance = MagicMock()
        mock_app_instance.bot = MagicMock()
        mock_app_instance.bot.send_message = AsyncMock()
        mock_app.builder.return_value.token.return_value.build.return_value = mock_app_instance

        mock_skill_service = MagicMock()
        mock_logging_service = MagicMock()
        mock_logging_service.log_action = AsyncMock()

        return TelegramBotInterface(
            config=config,
            sqs_client=MagicMock(),
            queue_manager=MagicMock(),
            agent_service=MagicMock(),
            logging_service=mock_logging_service,
            skill_service=mock_skill_service,
        )

    @pytest.mark.asyncio
    async def test_bot_delegates_skills_to_handler(self, bot):
        """Test bot delegates /skills command to message_handlers."""
        bot.skill_service.list_skills.return_value = ["weather", "calculator"]

        update = MagicMock()
        update.effective_user.id = 111
        update.effective_user.username = "testuser"
        update.effective_chat.id = 123
        context = MagicMock()

        # Mock the whitelist check to return False (not rejected)
        async def mock_reject(*args):
            return False

        with patch.object(
            bot._message_handlers, "reject_if_not_whitelisted", side_effect=mock_reject
        ):
            with patch(
                "app.telegram.message_sender.TelegramMessageSender.send_response",
                new_callable=AsyncMock,
            ) as mock_send:
                await bot._handle_skills_command(update, context)

                mock_send.assert_called_once()
                # Get the response text from the call
                response = mock_send.call_args[0][1]
                assert "weather" in response
                assert "calculator" in response
