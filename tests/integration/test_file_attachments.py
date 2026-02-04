"""Integration tests for file attachment handling.

Tests verify the complete file attachment flow:
- Document upload → download → process → verify context
- Photo upload → resolution selection → download → process
- File cleanup → verify deletion

Requirements: 1.1, 1.4, 2.1, 2.5, 2.6, 4.1, 4.6, 10.5
"""

import json
import os
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import boto3
import pytest
import pytest_asyncio
from botocore.config import Config

from app.config import AgentConfig
from app.enums import ModelProvider
from app.services.file_service import FileService, FileMetadata
from app.sqs.message_processor import MessageProcessor
from app.sqs.queue_manager import SQSQueueManager


# LocalStack configuration
LOCALSTACK_ENDPOINT = os.environ.get(
    "LOCALSTACK_ENDPOINT", "http://sqs.us-east-1.localhost.localstack.cloud:4566"
)


def get_localstack_sqs_client():
    """Create an SQS client configured for LocalStack."""
    return boto3.client(
        "sqs",
        endpoint_url=LOCALSTACK_ENDPOINT,
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=Config(retries={"max_attempts": 3, "mode": "standard"}),
    )


def is_localstack_available() -> bool:
    """Check if LocalStack is running and accessible."""
    try:
        client = get_localstack_sqs_client()
        client.list_queues()
        return True
    except Exception:
        return False


# Skip all tests if LocalStack is not available
pytestmark = pytest.mark.skipif(
    not is_localstack_available(),
    reason="LocalStack is not available at " + LOCALSTACK_ENDPOINT,
)


class TestDocumentAttachmentFlow:
    """Integration tests for document attachment flow.

    Tests: Send document → download → process → verify context

    Requirements: 1.1, 1.4, 4.1
    """

    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories for file storage."""
        temp_base = tempfile.mkdtemp()
        work_base = tempfile.mkdtemp()
        yield temp_base, work_base
        shutil.rmtree(temp_base, ignore_errors=True)
        shutil.rmtree(work_base, ignore_errors=True)

    @pytest.fixture
    def config(self, temp_dirs):
        """Create test configuration with file attachment settings."""
        temp_base, work_base = temp_dirs
        return MagicMock(
            spec=AgentConfig,
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            telegram_bot_token="test-bot-token",
            enable_file_attachments=True,
            max_file_size_mb=20,
            file_retention_hours=24,
            allowed_file_extensions=[
                ".txt", ".pdf", ".csv", ".json", ".xml", ".md",
                ".py", ".js", ".ts", ".html", ".css",
                ".png", ".jpg", ".jpeg", ".gif", ".webp",
            ],
            temp_files_base_dir=temp_base,
            working_folder_base_dir=work_base,
        )

    @pytest.fixture
    def file_service(self, config):
        """Create FileService instance."""
        return FileService(config)

    @pytest.fixture
    def sqs_client(self):
        """Create a fresh SQS client for each test."""
        return get_localstack_sqs_client()

    @pytest.fixture
    def queue_manager(self, sqs_client) -> SQSQueueManager:
        """Create a queue manager with unique prefix for test isolation."""
        prefix = f"file-{uuid.uuid4().hex[:8]}-"
        return SQSQueueManager(sqs_client, queue_prefix=prefix)

    @pytest.fixture
    def cleanup_queues(self, sqs_client, queue_manager):
        """Cleanup all test queues after test completion."""
        yield
        for queue_url in queue_manager.get_all_queue_urls():
            try:
                sqs_client.delete_queue(QueueUrl=queue_url)
            except Exception:
                pass

    @pytest.mark.asyncio
    async def test_document_attachment_enqueue_to_sqs(
        self, sqs_client, queue_manager, file_service, cleanup_queues
    ):
        """Test that document attachments are correctly enqueued to SQS.

        Verifies: Document metadata is included in SQS message payload.

        Requirements: 1.1, 1.4, 6.1
        """
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        chat_id = 12345
        caption = "Here's my document"

        # Create a test file in user's temp directory
        temp_dir = file_service.get_user_temp_dir(user_id)
        test_file = temp_dir / "test_document.txt"
        test_file.write_text("Test document content")

        # Create attachment metadata
        attachment = FileMetadata(
            file_id="test_file_id_123",
            file_name="test_document.txt",
            file_path=str(test_file),
            mime_type="text/plain",
            file_size=test_file.stat().st_size,
            is_image=False,
        )

        # Get or create queue for user
        queue_url = queue_manager.get_or_create_queue(user_id)

        # Simulate what TelegramBotInterface._enqueue_message_with_attachments does
        attachments_data = [
            {
                "file_id": attachment.file_id,
                "file_name": attachment.file_name,
                "file_path": attachment.file_path,
                "mime_type": attachment.mime_type,
                "file_size": attachment.file_size,
                "is_image": attachment.is_image,
            }
        ]

        payload = {
            "user_id": user_id,
            "message": caption,
            "chat_id": chat_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "attachments": attachments_data,
        }
        sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(payload),
        )

        # Verify message is in queue with attachment data
        response = sqs_client.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=5,
        )

        assert "Messages" in response
        assert len(response["Messages"]) == 1

        body = json.loads(response["Messages"][0]["Body"])
        assert body["user_id"] == user_id
        assert body["message"] == caption
        assert "attachments" in body
        assert len(body["attachments"]) == 1

        att = body["attachments"][0]
        assert att["file_id"] == "test_file_id_123"
        assert att["file_name"] == "test_document.txt"
        assert att["mime_type"] == "text/plain"
        assert att["is_image"] is False

    @pytest.mark.asyncio
    async def test_document_processor_routes_with_attachments(
        self, sqs_client, queue_manager, file_service, cleanup_queues
    ):
        """Test that MessageProcessor routes messages with attachments to agent.

        Verifies: Agent receives attachment context for processing.

        Requirements: 1.4, 4.1, 6.3
        """
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        chat_id = 12345
        caption = "Process this document"

        # Create a test file
        temp_dir = file_service.get_user_temp_dir(user_id)
        test_file = temp_dir / "process_me.txt"
        test_file.write_text("Content to process")

        # Create queue and send message with attachment
        queue_url = queue_manager.get_or_create_queue(user_id)
        payload = {
            "user_id": user_id,
            "message": caption,
            "chat_id": chat_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "attachments": [
                {
                    "file_id": "doc_123",
                    "file_name": "process_me.txt",
                    "file_path": str(test_file),
                    "mime_type": "text/plain",
                    "file_size": test_file.stat().st_size,
                    "is_image": False,
                }
            ],
        }
        sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(payload),
        )

        # Create mock agent service
        mock_agent_service = AsyncMock()
        mock_agent_service.process_message = AsyncMock(return_value="Processed")
        mock_agent_service.process_message_with_attachments = AsyncMock(
            return_value="Processed with attachment"
        )

        # Create processor
        processor = MessageProcessor(
            sqs_client=sqs_client,
            queue_manager=queue_manager,
            agent_service=mock_agent_service,
            polling_interval=0.1,
        )

        # Process the queue
        await processor._process_queue(queue_url)

        # Verify agent service was called with attachments
        mock_agent_service.process_message_with_attachments.assert_called_once()
        call_args = mock_agent_service.process_message_with_attachments.call_args
        assert call_args.kwargs["user_id"] == user_id
        assert call_args.kwargs["message"] == caption
        assert len(call_args.kwargs["attachments"]) == 1


class TestPhotoAttachmentFlow:
    """Integration tests for photo attachment flow.

    Tests: Send photo → select resolution → download → process

    Requirements: 2.1, 2.5, 2.6
    """

    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories for file storage."""
        temp_base = tempfile.mkdtemp()
        work_base = tempfile.mkdtemp()
        yield temp_base, work_base
        shutil.rmtree(temp_base, ignore_errors=True)
        shutil.rmtree(work_base, ignore_errors=True)

    @pytest.fixture
    def config(self, temp_dirs):
        """Create test configuration."""
        temp_base, work_base = temp_dirs
        return MagicMock(
            spec=AgentConfig,
            enable_file_attachments=True,
            max_file_size_mb=20,
            file_retention_hours=24,
            allowed_file_extensions=[
                ".txt", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp",
            ],
            temp_files_base_dir=temp_base,
            working_folder_base_dir=work_base,
            vision_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
        )

    @pytest.fixture
    def file_service(self, config):
        """Create FileService instance."""
        return FileService(config)

    @pytest.fixture
    def sqs_client(self):
        """Create a fresh SQS client for each test."""
        return get_localstack_sqs_client()

    @pytest.fixture
    def queue_manager(self, sqs_client) -> SQSQueueManager:
        """Create a queue manager with unique prefix."""
        prefix = f"photo-{uuid.uuid4().hex[:8]}-"
        return SQSQueueManager(sqs_client, queue_prefix=prefix)

    @pytest.fixture
    def cleanup_queues(self, sqs_client, queue_manager):
        """Cleanup all test queues after test completion."""
        yield
        for queue_url in queue_manager.get_all_queue_urls():
            try:
                sqs_client.delete_queue(QueueUrl=queue_url)
            except Exception:
                pass

    @pytest.mark.asyncio
    async def test_photo_attachment_enqueue_with_image_flag(
        self, sqs_client, queue_manager, file_service, cleanup_queues
    ):
        """Test that photo attachments are enqueued with is_image=True.

        Verifies: Image flag is correctly set for photo attachments.

        Requirements: 2.1, 2.6
        """
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        chat_id = 12345
        caption = "Check out this photo"

        # Create a test image file
        temp_dir = file_service.get_user_temp_dir(user_id)
        test_image = temp_dir / "photo_abc123.jpg"
        test_image.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)  # JPEG header

        # Get or create queue
        queue_url = queue_manager.get_or_create_queue(user_id)

        # Simulate photo attachment payload
        payload = {
            "user_id": user_id,
            "message": caption,
            "chat_id": chat_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "attachments": [
                {
                    "file_id": "photo_abc123",
                    "file_name": "photo_abc123.jpg",
                    "file_path": str(test_image),
                    "mime_type": "image/jpeg",
                    "file_size": test_image.stat().st_size,
                    "is_image": True,
                }
            ],
        }
        sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(payload),
        )

        # Verify message
        response = sqs_client.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=5,
        )

        body = json.loads(response["Messages"][0]["Body"])
        assert body["attachments"][0]["is_image"] is True
        assert body["attachments"][0]["mime_type"] == "image/jpeg"

    @pytest.mark.asyncio
    async def test_photo_processor_routes_to_vision_processing(
        self, sqs_client, queue_manager, file_service, cleanup_queues
    ):
        """Test that photos are routed for vision model processing.

        Verifies: Image attachments trigger vision processing path.

        Requirements: 2.5, 2.6
        """
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        chat_id = 12345
        caption = "What's in this image?"

        # Create a test image
        temp_dir = file_service.get_user_temp_dir(user_id)
        test_image = temp_dir / "analyze_me.jpg"
        test_image.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

        # Create queue and send message
        queue_url = queue_manager.get_or_create_queue(user_id)
        payload = {
            "user_id": user_id,
            "message": caption,
            "chat_id": chat_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "attachments": [
                {
                    "file_id": "img_456",
                    "file_name": "analyze_me.jpg",
                    "file_path": str(test_image),
                    "mime_type": "image/jpeg",
                    "file_size": test_image.stat().st_size,
                    "is_image": True,
                }
            ],
        }
        sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(payload),
        )

        # Create mock agent service
        mock_agent_service = AsyncMock()
        mock_agent_service.process_message_with_attachments = AsyncMock(
            return_value="I see a beautiful landscape"
        )

        # Create processor
        processor = MessageProcessor(
            sqs_client=sqs_client,
            queue_manager=queue_manager,
            agent_service=mock_agent_service,
            polling_interval=0.1,
        )

        # Process the queue
        await processor._process_queue(queue_url)

        # Verify vision processing was triggered
        mock_agent_service.process_message_with_attachments.assert_called_once()
        call_args = mock_agent_service.process_message_with_attachments.call_args
        attachments = call_args.kwargs["attachments"]
        assert len(attachments) == 1
        assert attachments[0].is_image is True


class TestFileCleanup:
    """Integration tests for file cleanup functionality.

    Tests: Create old files → run cleanup → verify deletion

    Requirements: 4.6, 10.5
    """

    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories for file storage."""
        temp_base = tempfile.mkdtemp()
        work_base = tempfile.mkdtemp()
        yield temp_base, work_base
        shutil.rmtree(temp_base, ignore_errors=True)
        shutil.rmtree(work_base, ignore_errors=True)

    @pytest.fixture
    def config(self, temp_dirs):
        """Create test configuration."""
        temp_base, work_base = temp_dirs
        return MagicMock(
            spec=AgentConfig,
            enable_file_attachments=True,
            max_file_size_mb=20,
            file_retention_hours=24,
            allowed_file_extensions=[".txt", ".pdf", ".png", ".jpg"],
            temp_files_base_dir=temp_base,
            working_folder_base_dir=work_base,
        )

    @pytest.fixture
    def file_service(self, config):
        """Create FileService instance."""
        return FileService(config)

    @pytest.mark.asyncio
    async def test_cleanup_deletes_old_files_in_temp_dir(self, file_service):
        """Test that cleanup deletes old files from temp directories.

        Verifies: Files older than retention period are deleted.

        Requirements: 4.6, 10.5
        """
        # Create a user directory with only old files (will be deleted entirely)
        old_user_id = "cleanup_test_user_old"
        old_temp_dir = file_service.get_user_temp_dir(old_user_id)

        old_file1 = old_temp_dir / "old_file1.txt"
        old_file2 = old_temp_dir / "old_file2.txt"

        old_file1.write_text("Old content 1")
        old_file2.write_text("Old content 2")

        # Set old files to 2 hours ago
        old_time = time.time() - (2 * 3600)
        os.utime(old_file1, (old_time, old_time))
        os.utime(old_file2, (old_time, old_time))

        # Create a user directory with recent files (will be preserved)
        recent_user_id = "cleanup_test_user_recent"
        recent_temp_dir = file_service.get_user_temp_dir(recent_user_id)
        recent_file = recent_temp_dir / "recent_file.txt"
        recent_file.write_text("Recent content")

        # Run cleanup with 1 hour max age
        deleted_count = await file_service.cleanup_old_files(max_age_hours=1)

        # Verify old user directory was deleted, recent directory preserved
        assert deleted_count >= 2
        assert not old_temp_dir.exists()
        assert recent_temp_dir.exists()
        assert recent_file.exists()

    @pytest.mark.asyncio
    async def test_cleanup_deletes_old_files_in_working_dir(self, file_service):
        """Test that cleanup deletes old files from working directories.

        Verifies: Working folder files are also cleaned up.

        Requirements: 10.5
        """
        # Create a user directory with only old files in working dir
        old_user_id = "cleanup_work_user_old"
        old_work_dir = file_service.get_user_working_dir(old_user_id)
        old_work_file = old_work_dir / "old_work.txt"
        old_work_file.write_text("Old work content")

        # Set old file to 2 hours ago
        old_time = time.time() - (2 * 3600)
        os.utime(old_work_file, (old_time, old_time))

        # Create a user directory with recent files in working dir
        recent_user_id = "cleanup_work_user_recent"
        recent_work_dir = file_service.get_user_working_dir(recent_user_id)
        recent_work_file = recent_work_dir / "recent_work.txt"
        recent_work_file.write_text("Recent work content")

        # Run cleanup
        deleted_count = await file_service.cleanup_old_files(max_age_hours=1)

        # Verify old user directory was deleted, recent directory preserved
        assert deleted_count >= 1
        assert not old_work_dir.exists()
        assert recent_work_dir.exists()
        assert recent_work_file.exists()

    @pytest.mark.asyncio
    async def test_cleanup_handles_multiple_users(self, file_service):
        """Test that cleanup works across multiple user directories.

        Verifies: Cleanup processes all user directories.

        Requirements: 4.6
        """
        users = ["user_a", "user_b", "user_c"]
        old_files = []

        # Create old files for each user
        for user_id in users:
            temp_dir = file_service.get_user_temp_dir(user_id)
            old_file = temp_dir / f"old_{user_id}.txt"
            old_file.write_text(f"Old content for {user_id}")

            old_time = time.time() - (2 * 3600)
            os.utime(old_file, (old_time, old_time))
            old_files.append(old_file)

        # Run cleanup
        deleted_count = await file_service.cleanup_old_files(max_age_hours=1)

        # Verify all old files deleted
        assert deleted_count >= len(users)
        for old_file in old_files:
            assert not old_file.exists()

    def test_clear_working_folder_on_new_session(self, file_service):
        """Test that working folder is cleared when starting new session.

        Verifies: All files in working folder are deleted on clear.

        Requirements: 10.4
        """
        user_id = "session_clear_user"
        work_dir = file_service.get_user_working_dir(user_id)

        # Create files in working directory
        (work_dir / "file1.txt").write_text("Content 1")
        (work_dir / "file2.txt").write_text("Content 2")
        (work_dir / "file3.txt").write_text("Content 3")

        # Verify files exist
        assert len(list(work_dir.iterdir())) == 3

        # Clear working folder (simulating new session)
        file_service.clear_working_folder(user_id)

        # Verify all files deleted but directory exists
        assert work_dir.exists()
        assert len(list(work_dir.iterdir())) == 0
