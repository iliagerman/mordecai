"""Unit tests for Telegram bot.

Tests Telegram bot functionality including:
- Property 24: Message Enqueue to User Queue

Requirements: 11.5, 11.6, 11.2, 12.2
"""

import json
import shutil
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import given, strategies as st, settings

from app.config import AgentConfig
from app.enums import CommandType, ModelProvider
from app.services.command_parser import CommandParser, ParsedCommand
from app.telegram.bot import TelegramBotInterface


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
        service = MagicMock()
        return service

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


class TestTelegramBotCommandExecution:
    """Tests for command execution routing.

    Tests that commands are correctly routed to handlers.
    **Validates: Requirements 11.5, 11.6**
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
    def mock_sqs_client(self):
        """Create mock SQS client."""
        return MagicMock()

    @pytest.fixture
    def mock_queue_manager(self):
        """Create mock queue manager."""
        manager = MagicMock()
        manager.get_or_create_queue = MagicMock(return_value="https://queue-url")
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
        """Create TelegramBotInterface instance."""
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
    async def test_execute_command_routes_message_to_enqueue(self, bot, mock_sqs_client):
        """Test MESSAGE command type routes to _enqueue_message."""
        parsed = ParsedCommand(CommandType.MESSAGE, ["Hello agent"])

        with patch.object(bot, "_enqueue_message", new_callable=AsyncMock) as mock_enqueue:
            await bot._execute_command(parsed, "user-1", 123, "Hello agent")
            mock_enqueue.assert_called_once_with("user-1", 123, "Hello agent")

    @pytest.mark.asyncio
    async def test_execute_command_routes_new_to_handler(self, bot, mock_agent_service):
        """Test NEW command type routes to new session handler."""
        parsed = ParsedCommand(CommandType.NEW)

        with patch.object(bot, "send_response", new_callable=AsyncMock):
            await bot._execute_command(parsed, "user-1", 123, "new")
            mock_agent_service.new_session.assert_called_once_with("user-1")

    @pytest.mark.asyncio
    async def test_execute_command_routes_logs_to_handler(self, bot, mock_logging_service):
        """Test LOGS command type routes to logs handler."""
        parsed = ParsedCommand(CommandType.LOGS)

        with patch.object(bot, "send_response", new_callable=AsyncMock):
            await bot._execute_command(parsed, "user-1", 123, "logs")
            mock_logging_service.get_recent_logs.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_command_routes_help_to_handler(self, bot):
        """Test HELP command type sends help text."""
        parsed = ParsedCommand(CommandType.HELP)

        with patch.object(bot, "send_response", new_callable=AsyncMock) as mock_send:
            await bot._execute_command(parsed, "user-1", 123, "help")
            mock_send.assert_called_once()
            # Verify help text contains command info
            help_text = mock_send.call_args[0][1]
            assert "new" in help_text.lower()


class TestTelegramBotSkillCommands:
    """Tests for skill management slash commands.

    Tests /skills, /add_skill, /delete_skill commands.
    **Validates: Requirements 11.5, 11.6**
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

        bot = TelegramBotInterface(
            config=config,
            sqs_client=MagicMock(),
            queue_manager=MagicMock(),
            agent_service=MagicMock(),
            logging_service=mock_logging_service,
            skill_service=mock_skill_service,
        )
        return bot

    @pytest.mark.asyncio
    async def test_skills_command_lists_installed_skills(self, bot):
        """Test /skills command lists all installed skills."""
        bot.skill_service.list_skills.return_value = ["weather", "calculator"]

        update = MagicMock()
        update.effective_user.username = "testuser"
        update.effective_chat.id = 123
        context = MagicMock()

        with patch.object(bot, "send_response", new_callable=AsyncMock) as mock_send:
            await bot._handle_skills_command(update, context)

            mock_send.assert_called_once()
            response = mock_send.call_args[0][1]
            assert "weather" in response
            assert "calculator" in response

    @pytest.mark.asyncio
    async def test_skills_command_empty_list(self, bot):
        """Test /skills command when no skills installed."""
        bot.skill_service.list_skills.return_value = []

        update = MagicMock()
        update.effective_user.username = "testuser"
        update.effective_chat.id = 123
        context = MagicMock()

        with patch.object(bot, "send_response", new_callable=AsyncMock) as mock_send:
            await bot._handle_skills_command(update, context)

            mock_send.assert_called_once()
            response = mock_send.call_args[0][1]
            assert "no skills" in response.lower()

    @pytest.mark.asyncio
    async def test_add_skill_command_installs_skill(self, bot):
        """Test /add_skill command installs skill from URL."""
        from app.models.domain import SkillMetadata

        md = SkillMetadata(
            name="weather",
            source_url="https://github.com/user/repo/tree/main/weather",
            installed_at=datetime.now(timezone.utc),
        )

        bot.skill_service.download_skill_to_pending.return_value = {
            "ok": True,
            "scope": "user",
            "pending_dir": "/tmp/pending/weather",
            "metadata": md,
        }

        update = MagicMock()
        update.effective_user.username = "testuser"
        update.effective_chat.id = 123
        context = MagicMock()
        context.args = ["https://github.com/user/repo/tree/main/weather"]

        with patch.object(bot, "send_response", new_callable=AsyncMock):
            await bot._handle_add_skill_command(update, context)

            bot.skill_service.download_skill_to_pending.assert_called_once_with(
                "https://github.com/user/repo/tree/main/weather",
                "testuser",
                scope="user",
            )

    @pytest.mark.asyncio
    async def test_add_skill_command_no_url_shows_usage(self, bot):
        """Test /add_skill without URL shows usage message."""
        update = MagicMock()
        update.effective_user.username = "testuser"
        update.effective_chat.id = 123
        context = MagicMock()
        context.args = []

        with patch.object(bot, "send_response", new_callable=AsyncMock) as mock_send:
            await bot._handle_add_skill_command(update, context)

            mock_send.assert_called_once()
            response = mock_send.call_args[0][1]
            assert "usage" in response.lower()

    @pytest.mark.asyncio
    async def test_delete_skill_command_removes_skill(self, bot):
        """Test /delete_skill command removes installed skill."""
        bot.skill_service.uninstall_skill.return_value = "Skill 'weather' uninstalled"

        update = MagicMock()
        update.effective_user.username = "testuser"
        update.effective_chat.id = 123
        context = MagicMock()
        context.args = ["weather"]

        with patch.object(bot, "send_response", new_callable=AsyncMock):
            await bot._handle_delete_skill_command(update, context)

            bot.skill_service.uninstall_skill.assert_called_once_with("weather", "testuser")

    @pytest.mark.asyncio
    async def test_delete_skill_command_no_name_shows_usage(self, bot):
        """Test /delete_skill without name shows usage message."""
        update = MagicMock()
        update.effective_user.username = "testuser"
        update.effective_chat.id = 123
        context = MagicMock()
        context.args = []

        with patch.object(bot, "send_response", new_callable=AsyncMock) as mock_send:
            await bot._handle_delete_skill_command(update, context)

            mock_send.assert_called_once()
            response = mock_send.call_args[0][1]
            assert "usage" in response.lower()


class TestTelegramBotEnqueueProperty:
    """Property-based tests for message enqueueing.

    **Property 24: Message Enqueue to User Queue**
    *For any* message sent via Telegram, it should be enqueued to the
    correct user's SQS queue.
    **Validates: Requirements 11.2, 12.2**
    """

    @given(
        user_id=st.text(
            alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
            min_size=1,
            max_size=50,
        ),
        chat_id=st.integers(min_value=1, max_value=10**12),
        message=st.text(min_size=1, max_size=500).filter(lambda x: x.strip()),
    )
    @settings(max_examples=100)
    @pytest.mark.asyncio
    @patch("app.telegram.bot.Application")
    async def test_property_message_enqueued_to_correct_user_queue(
        self, mock_app, user_id: str, chat_id: int, message: str
    ):
        """Property: Any message is enqueued to the correct user's queue."""
        temp_dir = tempfile.mkdtemp()
        try:
            # Setup mocks
            mock_sqs_client = MagicMock()
            mock_sqs_client.send_message = MagicMock(return_value={"MessageId": "id"})

            expected_queue_url = f"https://sqs.us-east-1.amazonaws.com/123/agent-user-{user_id}"
            mock_queue_manager = MagicMock()
            mock_queue_manager.get_or_create_queue = MagicMock(return_value=expected_queue_url)

            mock_logging_service = MagicMock()
            mock_logging_service.log_action = AsyncMock()

            mock_app_instance = MagicMock()
            mock_app_instance.bot = MagicMock()
            mock_app_instance.bot.send_message = AsyncMock()
            mock_app.builder.return_value.token.return_value.build.return_value = mock_app_instance

            config = AgentConfig(
                model_provider=ModelProvider.BEDROCK,
                bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                telegram_bot_token="test-token",
                session_storage_dir=temp_dir,
                skills_base_dir=temp_dir,
            )

            bot = TelegramBotInterface(
                config=config,
                sqs_client=mock_sqs_client,
                queue_manager=mock_queue_manager,
                agent_service=MagicMock(),
                logging_service=mock_logging_service,
                skill_service=MagicMock(),
            )

            await bot._enqueue_message(user_id, chat_id, message)

            # Verify queue was requested for correct user
            mock_queue_manager.get_or_create_queue.assert_called_once_with(user_id)

            # Verify message was sent to correct queue
            call_args = mock_sqs_client.send_message.call_args
            assert call_args.kwargs["QueueUrl"] == expected_queue_url

            # Verify message body contains correct user_id
            body = json.loads(call_args.kwargs["MessageBody"])
            assert body["user_id"] == user_id
            assert body["chat_id"] == chat_id
            assert body["message"] == message

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @given(
        user_id=st.text(
            alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
            min_size=1,
            max_size=20,
        ),
        message=st.text(min_size=1, max_size=200).filter(lambda x: x.strip()),
    )
    @settings(max_examples=100)
    @pytest.mark.asyncio
    @patch("app.telegram.bot.Application")
    async def test_property_message_body_contains_required_fields(
        self, mock_app, user_id: str, message: str
    ):
        """Property: Enqueued message body always contains required fields."""
        temp_dir = tempfile.mkdtemp()
        try:
            mock_sqs_client = MagicMock()
            mock_sqs_client.send_message = MagicMock(return_value={"MessageId": "id"})

            mock_queue_manager = MagicMock()
            mock_queue_manager.get_or_create_queue = MagicMock(return_value="https://queue-url")

            mock_logging_service = MagicMock()
            mock_logging_service.log_action = AsyncMock()

            mock_app_instance = MagicMock()
            mock_app_instance.bot = MagicMock()
            mock_app.builder.return_value.token.return_value.build.return_value = mock_app_instance

            config = AgentConfig(
                model_provider=ModelProvider.BEDROCK,
                bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                telegram_bot_token="test-token",
                session_storage_dir=temp_dir,
                skills_base_dir=temp_dir,
            )

            bot = TelegramBotInterface(
                config=config,
                sqs_client=mock_sqs_client,
                queue_manager=mock_queue_manager,
                agent_service=MagicMock(),
                logging_service=mock_logging_service,
                skill_service=MagicMock(),
            )

            await bot._enqueue_message(user_id, 12345, message)

            # Verify message body structure
            call_args = mock_sqs_client.send_message.call_args
            body = json.loads(call_args.kwargs["MessageBody"])

            # All required fields must be present
            assert "user_id" in body
            assert "message" in body
            assert "chat_id" in body
            assert "timestamp" in body

            # Timestamp must be valid ISO format
            timestamp = datetime.fromisoformat(body["timestamp"].replace("Z", "+00:00"))
            assert timestamp is not None

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @given(
        messages=st.lists(
            st.tuples(
                st.text(
                    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
                    min_size=1,
                    max_size=10,
                ),
                st.text(min_size=1, max_size=100).filter(lambda x: x.strip()),
            ),
            min_size=1,
            max_size=5,
        )
    )
    @settings(max_examples=100)
    @pytest.mark.asyncio
    @patch("app.telegram.bot.Application")
    async def test_property_each_user_gets_own_queue(
        self, mock_app, messages: list[tuple[str, str]]
    ):
        """Property: Each user's message goes to their own queue."""
        temp_dir = tempfile.mkdtemp()
        try:
            mock_sqs_client = MagicMock()
            mock_sqs_client.send_message = MagicMock(return_value={"MessageId": "id"})

            # Track which queue was requested for each user
            queue_requests = []

            def track_queue_request(user_id):
                queue_requests.append(user_id)
                return f"https://queue-url/{user_id}"

            mock_queue_manager = MagicMock()
            mock_queue_manager.get_or_create_queue = MagicMock(side_effect=track_queue_request)

            mock_logging_service = MagicMock()
            mock_logging_service.log_action = AsyncMock()

            mock_app_instance = MagicMock()
            mock_app_instance.bot = MagicMock()
            mock_app.builder.return_value.token.return_value.build.return_value = mock_app_instance

            config = AgentConfig(
                model_provider=ModelProvider.BEDROCK,
                bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                telegram_bot_token="test-token",
                session_storage_dir=temp_dir,
                skills_base_dir=temp_dir,
            )

            bot = TelegramBotInterface(
                config=config,
                sqs_client=mock_sqs_client,
                queue_manager=mock_queue_manager,
                agent_service=MagicMock(),
                logging_service=mock_logging_service,
                skill_service=MagicMock(),
            )

            # Enqueue messages for different users
            for user_id, message in messages:
                await bot._enqueue_message(user_id, 123, message)

            # Verify each user's queue was requested
            for user_id, _ in messages:
                assert user_id in queue_requests

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestTelegramBotHtmlFormatting:
    """Tests for HTML formatting of Telegram messages.

    Tests the _format_for_telegram_html method that converts
    markdown to Telegram-compatible HTML.
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
    @patch("app.telegram.bot.Application")
    def bot(self, mock_app, config):
        """Create TelegramBotInterface instance."""
        mock_app_instance = MagicMock()
        mock_app_instance.bot = MagicMock()
        mock_app.builder.return_value.token.return_value.build.return_value = mock_app_instance

        mock_logging_service = MagicMock()
        mock_logging_service.log_action = AsyncMock()

        return TelegramBotInterface(
            config=config,
            sqs_client=MagicMock(),
            queue_manager=MagicMock(),
            agent_service=MagicMock(),
            logging_service=mock_logging_service,
            skill_service=MagicMock(),
        )

    def test_escapes_html_special_characters(self, bot):
        """Test that HTML special characters are escaped."""
        text = "Use <script> and & symbols"
        result = bot._format_for_telegram_html(text)

        assert "&lt;script&gt;" in result
        assert "&amp;" in result

    def test_converts_headers_to_bold(self, bot):
        """Test markdown headers are converted to bold HTML."""
        text = "# Header 1\n## Header 2\n### Header 3"
        result = bot._format_for_telegram_html(text)

        assert "<b>Header 1</b>" in result
        assert "<b>Header 2</b>" in result
        assert "<b>Header 3</b>" in result

    def test_converts_bold_markdown(self, bot):
        """Test **bold** is converted to <b>bold</b>."""
        text = "This is **bold text** here"
        result = bot._format_for_telegram_html(text)

        assert "<b>bold text</b>" in result

    def test_converts_italic_markdown(self, bot):
        """Test *italic* is converted to <i>italic</i>."""
        text = "This is *italic text* here"
        result = bot._format_for_telegram_html(text)

        assert "<i>italic text</i>" in result

    def test_converts_inline_code(self, bot):
        """Test `code` is converted to <code>code</code>."""
        text = "Use `print()` function"
        result = bot._format_for_telegram_html(text)

        assert "<code>print()</code>" in result

    def test_converts_code_blocks(self, bot):
        """Test ```code blocks``` are converted to <pre>code</pre>."""
        text = "```python\nprint('hello')\n```"
        result = bot._format_for_telegram_html(text)

        assert "<pre>" in result
        assert "</pre>" in result
        assert "print" in result

    def test_converts_links(self, bot):
        """Test [text](url) is converted to <a href="url">text</a>."""
        text = "Check [Google](https://google.com) for info"
        result = bot._format_for_telegram_html(text)

        assert '<a href="https://google.com">Google</a>' in result

    def test_converts_markdown_table_to_pre(self, bot):
        """Test markdown tables are wrapped in <pre> for alignment."""
        text = """| Time | Sender | Subject |
|------|--------|---------|
| 16:49 | Google | Reminder |
| 17:27 | Apple | Update |"""
        result = bot._format_for_telegram_html(text)

        assert "<pre>" in result
        assert "</pre>" in result
        # Table content should be preserved
        assert "Time" in result
        assert "Sender" in result

    def test_plain_text_unchanged(self, bot):
        """Test plain text without markdown passes through."""
        text = "Just plain text here"
        result = bot._format_for_telegram_html(text)

        assert "Just plain text here" in result

    def test_mixed_formatting(self, bot):
        """Test text with multiple formatting types."""
        text = "# Title\nThis is **bold** and *italic* with `code`"
        result = bot._format_for_telegram_html(text)

        assert "<b>Title</b>" in result
        assert "<b>bold</b>" in result
        assert "<i>italic</i>" in result
        assert "<code>code</code>" in result


class TestTelegramBotHtmlFormattingProperty:
    """Property-based tests for HTML formatting."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    @given(text=st.text(min_size=0, max_size=500))
    @settings(max_examples=100)
    @patch("app.telegram.bot.Application")
    def test_property_html_output_never_contains_unescaped_lt_gt(self, mock_app, text: str):
        """Property: Output never contains unescaped < or > from input."""
        temp_dir = tempfile.mkdtemp()
        try:
            mock_app_instance = MagicMock()
            mock_app_instance.bot = MagicMock()
            mock_app.builder.return_value.token.return_value.build.return_value = mock_app_instance

            mock_logging_service = MagicMock()
            mock_logging_service.log_action = AsyncMock()

            config = AgentConfig(
                model_provider=ModelProvider.BEDROCK,
                bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                telegram_bot_token="test-token",
                session_storage_dir=temp_dir,
                skills_base_dir=temp_dir,
            )

            bot = TelegramBotInterface(
                config=config,
                sqs_client=MagicMock(),
                queue_manager=MagicMock(),
                agent_service=MagicMock(),
                logging_service=mock_logging_service,
                skill_service=MagicMock(),
            )

            result = bot._format_for_telegram_html(text)

            # Count < and > that are NOT part of valid HTML tags
            import re

            # Remove valid HTML tags we generate
            valid_tags = r'</?(?:b|i|code|pre|a(?:\s+href="[^"]*")?)>'
            cleaned = re.sub(valid_tags, "", result)

            # After removing valid tags, no < or > should remain
            # (they should all be escaped as &lt; or &gt;)
            assert "<" not in cleaned, f"Unescaped < found in: {result}"
            assert ">" not in cleaned, f"Unescaped > found in: {result}"

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @given(text=st.text(min_size=1, max_size=200).filter(lambda x: x.strip()))
    @settings(max_examples=100)
    @patch("app.telegram.bot.Application")
    def test_property_formatting_produces_string_output(self, mock_app, text: str):
        """Property: Formatting always produces a string output."""
        temp_dir = tempfile.mkdtemp()
        try:
            mock_app_instance = MagicMock()
            mock_app_instance.bot = MagicMock()
            mock_app.builder.return_value.token.return_value.build.return_value = mock_app_instance

            mock_logging_service = MagicMock()
            mock_logging_service.log_action = AsyncMock()

            config = AgentConfig(
                model_provider=ModelProvider.BEDROCK,
                bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                telegram_bot_token="test-token",
                session_storage_dir=temp_dir,
                skills_base_dir=temp_dir,
            )

            bot = TelegramBotInterface(
                config=config,
                sqs_client=MagicMock(),
                queue_manager=MagicMock(),
                agent_service=MagicMock(),
                logging_service=mock_logging_service,
                skill_service=MagicMock(),
            )

            result = bot._format_for_telegram_html(text)

            assert isinstance(result, str)
            assert len(result) > 0

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
