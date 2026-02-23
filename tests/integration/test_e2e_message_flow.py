"""End-to-end integration tests for message flow.

Tests verify the complete message flow:
Telegram → SQS → Agent → Response

This tests the integration between:
- TelegramBotInterface (message enqueueing)
- SQSQueueManager (queue management)
- MessageProcessor (message consumption and routing)
- AgentService (message processing)

Requirements: Integration
"""

import asyncio
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import boto3
import pytest
import pytest_asyncio
from botocore.config import Config

from app.config import AgentConfig
from app.enums import ModelProvider
from app.services.agent_service import AgentService
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


class TestEndToEndMessageFlow:
    """End-to-end tests for the complete message flow.

    Tests the integration: Telegram → SQS → Agent → Response

    Requirements: Integration
    """

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for session storage."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    @pytest.fixture
    def sqs_client(self):
        """Create a fresh SQS client for each test."""
        return get_localstack_sqs_client()

    @pytest.fixture
    def queue_manager(self, sqs_client) -> SQSQueueManager:
        """Create a queue manager with unique prefix for test isolation."""
        prefix = f"e2e-{uuid.uuid4().hex[:8]}-"
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

    @pytest.fixture
    def config(self, temp_dir):
        """Create test configuration."""
        return AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            telegram_bot_token="test-bot-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
            working_folder_base_dir=temp_dir,
        )

    @pytest_asyncio.fixture
    async def mock_agent_service(self):
        """Create a mock agent service that returns predictable responses."""
        service = AsyncMock()
        service.process_message = AsyncMock(
            side_effect=lambda user_id, message, onboarding_context=None: f"Response to: {message}"
        )
        service.new_session = MagicMock()
        return service

    @pytest.mark.asyncio
    async def test_message_enqueue_to_sqs(
        self, sqs_client, queue_manager, cleanup_queues
    ):
        """Test that messages are correctly enqueued to SQS.

        Verifies the first part of the flow: Telegram → SQS
        """
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        chat_id = 12345
        message = "Hello, agent!"

        # Get or create queue for user
        queue_url = queue_manager.get_or_create_queue(user_id)

        # Simulate what TelegramBotInterface._enqueue_message does
        payload = {
            "user_id": user_id,
            "message": message,
            "chat_id": chat_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(payload),
        )

        # Verify message is in queue
        response = sqs_client.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=5,
        )

        assert "Messages" in response
        assert len(response["Messages"]) == 1

        body = json.loads(response["Messages"][0]["Body"])
        assert body["user_id"] == user_id
        assert body["message"] == message
        assert body["chat_id"] == chat_id

    @pytest.mark.asyncio
    async def test_message_processor_consumes_and_routes(
        self, sqs_client, queue_manager, mock_agent_service, cleanup_queues
    ):
        """Test that MessageProcessor consumes messages and routes to agent.

        Verifies the second part of the flow: SQS → Agent
        """
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        chat_id = 12345
        message = "Process this message"

        # Create queue and send message
        queue_url = queue_manager.get_or_create_queue(user_id)
        payload = {
            "user_id": user_id,
            "message": message,
            "chat_id": chat_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(payload),
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

        # Verify agent service was called with correct parameters
        mock_agent_service.process_message.assert_called_once_with(
            user_id=user_id,
            message=message,
            onboarding_context=None,
        )

    @pytest.mark.asyncio
    async def test_response_callback_invoked(
        self, sqs_client, queue_manager, mock_agent_service, cleanup_queues
    ):
        """Test that response callback is invoked after processing.

        Verifies the final part of the flow: Agent → Response
        """
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        chat_id = 12345
        message = "Get response"

        # Create queue and send message
        queue_url = queue_manager.get_or_create_queue(user_id)
        payload = {
            "user_id": user_id,
            "message": message,
            "chat_id": chat_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(payload),
        )

        # Create callback mock
        response_callback = AsyncMock()

        # Create processor with callback
        processor = MessageProcessor(
            sqs_client=sqs_client,
            queue_manager=queue_manager,
            agent_service=mock_agent_service,
            response_callback=response_callback,
            polling_interval=0.1,
        )

        # Process the queue
        await processor._process_queue(queue_url)

        # Verify callback was invoked with chat_id and response
        response_callback.assert_called_once()
        actual_chat_id, actual_response = response_callback.call_args[0]
        assert actual_chat_id == chat_id
        assert actual_response == f"Response to: {message}"

    @pytest.mark.asyncio
    async def test_full_message_flow(
        self, sqs_client, queue_manager, mock_agent_service, cleanup_queues
    ):
        """Test the complete message flow from enqueue to response.

        Simulates: Telegram → SQS → Agent → Response
        """
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        chat_id = 67890
        messages = ["First message", "Second message", "Third message"]
        responses_received = []

        async def capture_response(chat_id: int, response: str):
            responses_received.append((chat_id, response))

        # Create queue
        queue_url = queue_manager.get_or_create_queue(user_id)

        # Enqueue all messages (simulating Telegram bot)
        for msg in messages:
            payload = {
                "user_id": user_id,
                "message": msg,
                "chat_id": chat_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            sqs_client.send_message(
                QueueUrl=queue_url,
                MessageBody=json.dumps(payload),
            )

        # Create processor with response capture
        processor = MessageProcessor(
            sqs_client=sqs_client,
            queue_manager=queue_manager,
            agent_service=mock_agent_service,
            response_callback=capture_response,
            polling_interval=0.1,
        )

        # Process all messages
        for _ in range(len(messages)):
            await processor._process_queue(queue_url)

        # Verify all messages were processed and responses received
        assert len(responses_received) == len(messages)
        for i, (recv_chat_id, response) in enumerate(responses_received):
            assert recv_chat_id == chat_id
            assert response == f"Response to: {messages[i]}"

    @pytest.mark.asyncio
    async def test_message_deleted_after_successful_processing(
        self, sqs_client, queue_manager, mock_agent_service, cleanup_queues
    ):
        """Test that messages are deleted from queue after successful processing."""
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        chat_id = 12345
        message = "Delete after processing"

        # Create queue and send message
        queue_url = queue_manager.get_or_create_queue(user_id)
        payload = {
            "user_id": user_id,
            "message": message,
            "chat_id": chat_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(payload),
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

        # Verify queue is empty
        response = sqs_client.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=1,
        )
        assert "Messages" not in response or len(response["Messages"]) == 0

    @pytest.mark.asyncio
    async def test_malformed_message_handling(
        self, sqs_client, queue_manager, mock_agent_service, cleanup_queues
    ):
        """Test that malformed messages are handled gracefully."""
        user_id = f"user-{uuid.uuid4().hex[:8]}"

        # Create queue and send malformed message
        queue_url = queue_manager.get_or_create_queue(user_id)
        sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody="not valid json",
        )

        # Create processor
        processor = MessageProcessor(
            sqs_client=sqs_client,
            queue_manager=queue_manager,
            agent_service=mock_agent_service,
            polling_interval=0.1,
        )

        # Process should not raise exception
        await processor._process_queue(queue_url)

        # Agent should not have been called
        mock_agent_service.process_message.assert_not_called()

        # Malformed message should be deleted
        response = sqs_client.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=1,
        )
        assert "Messages" not in response or len(response["Messages"]) == 0

    @pytest.mark.asyncio
    async def test_multiple_users_message_flow(
        self, sqs_client, queue_manager, mock_agent_service, cleanup_queues
    ):
        """Test message flow with multiple users simultaneously."""
        users = [
            {"user_id": f"user-{uuid.uuid4().hex[:8]}", "chat_id": 1001},
            {"user_id": f"user-{uuid.uuid4().hex[:8]}", "chat_id": 1002},
            {"user_id": f"user-{uuid.uuid4().hex[:8]}", "chat_id": 1003},
        ]
        responses_received = []

        async def capture_response(chat_id: int, response: str):
            responses_received.append((chat_id, response))

        # Create queues and send messages for each user
        for user in users:
            queue_url = queue_manager.get_or_create_queue(user["user_id"])
            payload = {
                "user_id": user["user_id"],
                "message": f"Message from {user['user_id']}",
                "chat_id": user["chat_id"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            sqs_client.send_message(
                QueueUrl=queue_url,
                MessageBody=json.dumps(payload),
            )

        # Create processor
        processor = MessageProcessor(
            sqs_client=sqs_client,
            queue_manager=queue_manager,
            agent_service=mock_agent_service,
            response_callback=capture_response,
            polling_interval=0.1,
        )

        # Process all queues
        for queue_url in queue_manager.get_all_queue_urls():
            await processor._process_queue(queue_url)

        # Verify all users received responses
        assert len(responses_received) == len(users)
        received_chat_ids = {r[0] for r in responses_received}
        expected_chat_ids = {u["chat_id"] for u in users}
        assert received_chat_ids == expected_chat_ids

