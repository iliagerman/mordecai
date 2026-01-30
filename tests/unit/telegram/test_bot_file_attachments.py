"""Unit tests for Telegram bot file attachment handling.

Tests file attachment functionality including:
- Property 6: Highest resolution photo selection
- Property 3: Attachment metadata in agent context
- Property 10: SQS payload structure for attachments
- Property 11: File send size routing
- Property 12: Error message content for size violations
- Property 13: Error message content for type violations

Requirements: 1.1, 1.3, 1.4, 1.6, 2.1, 2.5, 5.5, 6.1, 6.3, 6.4, 6.5, 8.2, 8.3
"""

import json
import shutil
import tempfile
from datetime import datetime, UTC
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import given, strategies as st, settings

from app.config import AgentConfig
from app.enums import ModelProvider
from app.services.file_service import FileMetadata, FileService
from app.telegram.bot import TelegramBotInterface


class TestPhotoResolutionSelection:
    """Tests for highest resolution photo selection.

    **Property 6: Highest resolution photo selection**
    *For any* photo message with multiple size variants, the system
    SHALL select and download the variant with the largest file size.
    **Validates: Requirements 2.5**
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
            temp_files_base_dir=temp_dir,
            working_folder_base_dir=temp_dir,
        )

    @pytest.fixture
    @patch("app.telegram.bot.Application")
    def bot(self, mock_app, config):
        """Create TelegramBotInterface instance."""
        mock_app_instance = MagicMock()
        mock_app_instance.bot = MagicMock()
        mock_app_instance.bot.send_message = AsyncMock()
        mock_app.builder.return_value.token.return_value.build.return_value = (
            mock_app_instance
        )

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

    def test_selects_largest_photo_from_list(self, bot):
        """Test that largest photo by file_size is selected."""
        # Create mock PhotoSize objects
        small = MagicMock()
        small.file_size = 1000
        small.width = 100
        small.height = 100

        medium = MagicMock()
        medium.file_size = 5000
        medium.width = 500
        medium.height = 500

        large = MagicMock()
        large.file_size = 20000
        large.width = 1920
        large.height = 1080

        photos = [small, medium, large]

        result = bot._select_highest_resolution_photo(photos)

        assert result == large
        assert result.file_size == 20000

    def test_selects_largest_when_unsorted(self, bot):
        """Test selection works regardless of list order."""
        large = MagicMock()
        large.file_size = 50000

        small = MagicMock()
        small.file_size = 1000

        medium = MagicMock()
        medium.file_size = 10000

        # Unsorted order
        photos = [medium, large, small]

        result = bot._select_highest_resolution_photo(photos)

        assert result == large

    @given(
        sizes=st.lists(
            st.integers(min_value=1, max_value=50_000_000),
            min_size=1,
            max_size=10,
        )
    )
    @settings(max_examples=100)
    @patch("app.telegram.bot.Application")
    def test_property_always_selects_largest(
        self,
        mock_app,
        sizes: list[int],
    ):
        """Property 6: Always selects photo with largest file_size."""
        temp_dir = tempfile.mkdtemp()
        try:
            mock_app_instance = MagicMock()
            mock_app_instance.bot = MagicMock()
            mock_app.builder.return_value.token.return_value.build.return_value = (
                mock_app_instance
            )

            mock_logging_service = MagicMock()
            mock_logging_service.log_action = AsyncMock()

            config = AgentConfig(
                model_provider=ModelProvider.BEDROCK,
                bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                telegram_bot_token="test-token",
                session_storage_dir=temp_dir,
                skills_base_dir=temp_dir,
                temp_files_base_dir=temp_dir,
                working_folder_base_dir=temp_dir,
            )

            bot = TelegramBotInterface(
                config=config,
                sqs_client=MagicMock(),
                queue_manager=MagicMock(),
                agent_service=MagicMock(),
                logging_service=mock_logging_service,
                skill_service=MagicMock(),
            )

            photos = []
            for size in sizes:
                photo = MagicMock()
                photo.file_size = size
                photos.append(photo)

            result = bot._select_highest_resolution_photo(photos)

            # Result should have the maximum file_size
            assert result.file_size == max(sizes)

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestSQSPayloadStructure:
    """Tests for SQS payload structure with attachments.

    **Property 3: Attachment metadata in agent context**
    **Property 10: SQS payload structure for attachments**
    *For any* message with attachments, the SQS payload SHALL include
    an "attachments" array with file metadata.
    **Validates: Requirements 1.3, 1.4, 6.1, 6.3, 6.4, 6.5**
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
            temp_files_base_dir=temp_dir,
            working_folder_base_dir=temp_dir,
        )

    @pytest.fixture
    def mock_sqs_client(self):
        """Create mock SQS client."""
        client = MagicMock()
        client.send_message = MagicMock(return_value={"MessageId": "test-id"})
        return client

    @pytest.fixture
    def mock_queue_manager(self):
        """Create mock queue manager."""
        manager = MagicMock()
        manager.get_or_create_queue = MagicMock(
            return_value="https://sqs.us-east-1.amazonaws.com/123/queue"
        )
        return manager

    @pytest.fixture
    @patch("app.telegram.bot.Application")
    def bot(
        self,
        mock_app,
        config,
        mock_sqs_client,
        mock_queue_manager,
    ):
        """Create TelegramBotInterface instance."""
        mock_app_instance = MagicMock()
        mock_app_instance.bot = MagicMock()
        mock_app_instance.bot.send_message = AsyncMock()
        mock_app.builder.return_value.token.return_value.build.return_value = (
            mock_app_instance
        )

        mock_logging_service = MagicMock()
        mock_logging_service.log_action = AsyncMock()

        return TelegramBotInterface(
            config=config,
            sqs_client=mock_sqs_client,
            queue_manager=mock_queue_manager,
            agent_service=MagicMock(),
            logging_service=mock_logging_service,
            skill_service=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_payload_includes_attachments_array(
        self,
        bot,
        mock_sqs_client,
    ):
        """Test SQS payload includes attachments array."""
        attachment = FileMetadata(
            file_id="test_file_id",
            file_name="document.pdf",
            file_path="/tmp/user1/document.pdf",
            mime_type="application/pdf",
            file_size=1024,
            is_image=False,
        )

        await bot._enqueue_message_with_attachments(
            user_id="user1",
            chat_id=123,
            message="Check this file",
            attachments=[attachment],
        )

        call_args = mock_sqs_client.send_message.call_args
        body = json.loads(call_args.kwargs["MessageBody"])

        assert "attachments" in body
        assert isinstance(body["attachments"], list)
        assert len(body["attachments"]) == 1

    @pytest.mark.asyncio
    async def test_attachment_contains_required_fields(
        self,
        bot,
        mock_sqs_client,
    ):
        """Test attachment metadata contains all required fields."""
        attachment = FileMetadata(
            file_id="file123",
            file_name="test.txt",
            file_path="/tmp/user1/test.txt",
            mime_type="text/plain",
            file_size=500,
            is_image=False,
        )

        await bot._enqueue_message_with_attachments(
            user_id="user1",
            chat_id=123,
            message="",
            attachments=[attachment],
        )

        call_args = mock_sqs_client.send_message.call_args
        body = json.loads(call_args.kwargs["MessageBody"])
        att = body["attachments"][0]

        # Verify all required fields (Requirement 1.3, 1.4)
        assert att["file_id"] == "file123"
        assert att["file_name"] == "test.txt"
        assert att["file_path"] == "/tmp/user1/test.txt"
        assert att["mime_type"] == "text/plain"
        assert att["file_size"] == 500
        assert att["is_image"] is False

    @pytest.mark.asyncio
    async def test_multiple_attachments_in_array(
        self,
        bot,
        mock_sqs_client,
    ):
        """Test multiple attachments are included in array."""
        attachments = [
            FileMetadata(
                file_id="file1",
                file_name="doc1.pdf",
                file_path="/tmp/user1/doc1.pdf",
                mime_type="application/pdf",
                file_size=1000,
                is_image=False,
            ),
            FileMetadata(
                file_id="file2",
                file_name="image.png",
                file_path="/tmp/user1/image.png",
                mime_type="image/png",
                file_size=2000,
                is_image=True,
            ),
        ]

        await bot._enqueue_message_with_attachments(
            user_id="user1",
            chat_id=123,
            message="Multiple files",
            attachments=attachments,
        )

        call_args = mock_sqs_client.send_message.call_args
        body = json.loads(call_args.kwargs["MessageBody"])

        # Requirement 6.5: Each attachment has separate entry
        assert len(body["attachments"]) == 2
        assert body["attachments"][0]["file_name"] == "doc1.pdf"
        assert body["attachments"][1]["file_name"] == "image.png"

    @pytest.mark.asyncio
    async def test_payload_includes_message_text(
        self,
        bot,
        mock_sqs_client,
    ):
        """Test payload includes text message with attachments."""
        attachment = FileMetadata(
            file_id="file1",
            file_name="doc.pdf",
            file_path="/tmp/user1/doc.pdf",
            mime_type="application/pdf",
            file_size=1000,
            is_image=False,
        )

        await bot._enqueue_message_with_attachments(
            user_id="user1",
            chat_id=123,
            message="Please review this document",
            attachments=[attachment],
        )

        call_args = mock_sqs_client.send_message.call_args
        body = json.loads(call_args.kwargs["MessageBody"])

        # Requirement 6.4: Support messages with text and attachments
        assert body["message"] == "Please review this document"
        assert len(body["attachments"]) == 1

    @given(
        num_attachments=st.integers(min_value=1, max_value=5),
        message=st.text(min_size=0, max_size=100),
    )
    @settings(max_examples=100)
    @pytest.mark.asyncio
    @patch("app.telegram.bot.Application")
    async def test_property_payload_structure(
        self,
        mock_app,
        num_attachments: int,
        message: str,
    ):
        """Property 10: SQS payload always has correct structure."""
        temp_dir = tempfile.mkdtemp()
        try:
            mock_sqs_client = MagicMock()
            mock_sqs_client.send_message = MagicMock(
                return_value={"MessageId": "id"}
            )

            mock_queue_manager = MagicMock()
            mock_queue_manager.get_or_create_queue = MagicMock(
                return_value="https://queue-url"
            )

            mock_logging_service = MagicMock()
            mock_logging_service.log_action = AsyncMock()

            mock_app_instance = MagicMock()
            mock_app_instance.bot = MagicMock()
            mock_app.builder.return_value.token.return_value.build.return_value = (
                mock_app_instance
            )

            config = AgentConfig(
                model_provider=ModelProvider.BEDROCK,
                bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                telegram_bot_token="test-token",
                session_storage_dir=temp_dir,
                skills_base_dir=temp_dir,
                temp_files_base_dir=temp_dir,
                working_folder_base_dir=temp_dir,
            )

            bot = TelegramBotInterface(
                config=config,
                sqs_client=mock_sqs_client,
                queue_manager=mock_queue_manager,
                agent_service=MagicMock(),
                logging_service=mock_logging_service,
                skill_service=MagicMock(),
            )

            attachments = [
                FileMetadata(
                    file_id=f"file{i}",
                    file_name=f"file{i}.txt",
                    file_path=f"/tmp/user/file{i}.txt",
                    mime_type="text/plain",
                    file_size=100 * (i + 1),
                    is_image=False,
                )
                for i in range(num_attachments)
            ]

            await bot._enqueue_message_with_attachments(
                user_id="user1",
                chat_id=123,
                message=message,
                attachments=attachments,
            )

            call_args = mock_sqs_client.send_message.call_args
            body = json.loads(call_args.kwargs["MessageBody"])

            # Property: Payload always has required structure
            assert "user_id" in body
            assert "message" in body
            assert "chat_id" in body
            assert "timestamp" in body
            assert "attachments" in body
            assert isinstance(body["attachments"], list)
            assert len(body["attachments"]) == num_attachments

            # Each attachment has required fields
            for att in body["attachments"]:
                assert "file_id" in att
                assert "file_name" in att
                assert "file_path" in att
                assert "mime_type" in att
                assert "file_size" in att
                assert "is_image" in att

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestFileSendRouting:
    """Tests for file send size routing.

    **Property 11: File send size routing**
    *For any* image file under 10MB, the system SHALL send it as a photo.
    *For any* image file 10MB or larger, the system SHALL send as document.
    **Validates: Requirements 5.5**
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
            temp_files_base_dir=temp_dir,
            working_folder_base_dir=temp_dir,
        )

    @pytest.fixture
    @patch("app.telegram.bot.Application")
    def bot(self, mock_app, config):
        """Create TelegramBotInterface instance."""
        mock_app_instance = MagicMock()
        mock_app_instance.bot = MagicMock()
        mock_app_instance.bot.send_message = AsyncMock()
        mock_app_instance.bot.send_photo = AsyncMock()
        mock_app_instance.bot.send_document = AsyncMock()
        mock_app.builder.return_value.token.return_value.build.return_value = (
            mock_app_instance
        )

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

    @pytest.mark.asyncio
    async def test_small_image_sent_as_photo(self, bot, temp_dir):
        """Test images under 10MB are sent as photos."""
        # Create a small test file (1KB)
        test_file = Path(temp_dir) / "small_image.jpg"
        test_file.write_bytes(b"x" * 1024)

        result = await bot.send_photo(123, test_file, "Small image")

        assert result is True
        bot.application.bot.send_photo.assert_called_once()

    @pytest.mark.asyncio
    async def test_large_image_sent_as_document(self, bot, temp_dir):
        """Test images 10MB+ are sent as documents."""
        # Create a large test file (11MB)
        test_file = Path(temp_dir) / "large_image.jpg"
        test_file.write_bytes(b"x" * (11 * 1024 * 1024))

        # Mock send_file to track the call
        with patch.object(
            bot, "send_file", new_callable=AsyncMock
        ) as mock_send_file:
            mock_send_file.return_value = True
            result = await bot.send_photo(123, test_file, "Large image")

            assert result is True
            mock_send_file.assert_called_once()

    @given(
        file_size_mb=st.integers(min_value=1, max_value=9),
    )
    @settings(max_examples=50)
    @pytest.mark.asyncio
    @patch("app.telegram.bot.Application")
    async def test_property_small_images_as_photo(
        self,
        mock_app,
        file_size_mb: int,
    ):
        """Property 11: Images under 10MB sent as photo."""
        temp_dir = tempfile.mkdtemp()
        try:
            mock_app_instance = MagicMock()
            mock_app_instance.bot = MagicMock()
            mock_app_instance.bot.send_message = AsyncMock()
            mock_app_instance.bot.send_photo = AsyncMock()
            mock_app_instance.bot.send_document = AsyncMock()
            mock_app.builder.return_value.token.return_value.build.return_value = (
                mock_app_instance
            )

            mock_logging_service = MagicMock()
            mock_logging_service.log_action = AsyncMock()

            config = AgentConfig(
                model_provider=ModelProvider.BEDROCK,
                bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                telegram_bot_token="test-token",
                session_storage_dir=temp_dir,
                skills_base_dir=temp_dir,
                temp_files_base_dir=temp_dir,
                working_folder_base_dir=temp_dir,
            )

            bot = TelegramBotInterface(
                config=config,
                sqs_client=MagicMock(),
                queue_manager=MagicMock(),
                agent_service=MagicMock(),
                logging_service=mock_logging_service,
                skill_service=MagicMock(),
            )

            # Create test file
            test_file = Path(temp_dir) / "test.jpg"
            test_file.write_bytes(b"x" * (file_size_mb * 1024 * 1024))

            await bot.send_photo(123, test_file)

            # Should be sent as photo (not document)
            mock_app_instance.bot.send_photo.assert_called()

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestErrorMessages:
    """Tests for error message content.

    **Property 12: Error message content for size violations**
    **Property 13: Error message content for type violations**
    **Validates: Requirements 8.2, 8.3**
    """

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    @pytest.fixture
    def config(self, temp_dir):
        """Create test configuration with specific limits."""
        return AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            telegram_bot_token="test-bot-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
            temp_files_base_dir=temp_dir,
            working_folder_base_dir=temp_dir,
            max_file_size_mb=20,
            allowed_file_extensions=[".txt", ".pdf", ".py"],
        )

    @pytest.fixture
    def file_service(self, config):
        """Create FileService instance."""
        return FileService(config)

    def test_size_error_contains_max_size(self, file_service, config):
        """Property 12: Size error message contains max size."""
        # File larger than limit
        result = file_service.validate_file(
            file_name="large.txt",
            file_size=25 * 1024 * 1024,  # 25MB
            mime_type="text/plain",
        )

        assert not result.valid
        assert str(config.max_file_size_mb) in result.error_message
        assert "20" in result.error_message

    def test_type_error_contains_allowed_extensions(
        self,
        file_service,
        config,
    ):
        """Property 13: Type error message lists allowed extensions."""
        result = file_service.validate_file(
            file_name="script.exe",
            file_size=1000,
            mime_type="application/x-executable",
        )

        assert not result.valid
        # Should list allowed extensions
        assert ".txt" in result.error_message
        assert ".pdf" in result.error_message
        assert ".py" in result.error_message

    @given(
        max_size=st.integers(min_value=1, max_value=100),
        file_size_over=st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=100)
    def test_property_size_error_always_contains_limit(
        self,
        max_size: int,
        file_size_over: int,
    ):
        """Property 12: Size error always contains the limit value."""
        temp_dir = tempfile.mkdtemp()
        try:
            config = AgentConfig(
                model_provider=ModelProvider.BEDROCK,
                bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                telegram_bot_token="test-token",
                session_storage_dir=temp_dir,
                skills_base_dir=temp_dir,
                temp_files_base_dir=temp_dir,
                working_folder_base_dir=temp_dir,
                max_file_size_mb=max_size,
            )

            file_service = FileService(config)

            # File size exceeds limit
            file_size = (max_size + file_size_over) * 1024 * 1024

            result = file_service.validate_file(
                file_name="test.txt",
                file_size=file_size,
                mime_type="text/plain",
            )

            assert not result.valid
            assert str(max_size) in result.error_message

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @given(
        extensions=st.lists(
            st.sampled_from([".txt", ".pdf", ".py", ".json", ".md"]),
            min_size=1,
            max_size=5,
            unique=True,
        ),
    )
    @settings(max_examples=100)
    def test_property_type_error_lists_all_allowed(
        self,
        extensions: list[str],
    ):
        """Property 13: Type error lists all allowed extensions."""
        temp_dir = tempfile.mkdtemp()
        try:
            config = AgentConfig(
                model_provider=ModelProvider.BEDROCK,
                bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                telegram_bot_token="test-token",
                session_storage_dir=temp_dir,
                skills_base_dir=temp_dir,
                temp_files_base_dir=temp_dir,
                working_folder_base_dir=temp_dir,
                allowed_file_extensions=extensions,
            )

            file_service = FileService(config)

            # Use an extension not in the list
            result = file_service.validate_file(
                file_name="malware.exe",
                file_size=1000,
                mime_type="application/x-executable",
            )

            assert not result.valid
            # All allowed extensions should be in error message
            for ext in extensions:
                assert ext in result.error_message

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
