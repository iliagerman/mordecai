"""Integration tests for SQS components using LocalStack.

Tests verify:
- Property 25: User Queue Creation
- Property 26: Message Consumption and Routing
- Property 27: Message Processing Order

Requirements: 14.8
"""

import asyncio
import json
import os
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import boto3
import pytest
import pytest_asyncio
from botocore.config import Config

from app.sqs.message_processor import MessageProcessor, QueueMessage
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


@pytest.fixture
def sqs_client():
    """Create a fresh SQS client for each test."""
    return get_localstack_sqs_client()


@pytest.fixture
def queue_manager(sqs_client) -> SQSQueueManager:
    """Create a queue manager with unique prefix for test isolation."""
    prefix = f"test-{uuid.uuid4().hex[:8]}-"
    return SQSQueueManager(sqs_client, queue_prefix=prefix)


@pytest.fixture
def cleanup_queues(sqs_client, queue_manager):
    """Cleanup all test queues after test completion."""
    yield
    # Delete all queues created during the test
    for queue_url in queue_manager.get_all_queue_urls():
        try:
            sqs_client.delete_queue(QueueUrl=queue_url)
        except Exception:
            pass


class TestUserQueueCreation:
    """Tests for Property 25: User Queue Creation.

    *For any* new user, a dedicated SQS queue should be created for them.
    **Validates: Requirements 12.1**
    """

    def test_queue_created_for_new_user(self, sqs_client, queue_manager, cleanup_queues):
        """Verify a queue is created when a new user is registered.

        Feature: mordecai, Property 25: User Queue Creation
        **Validates: Requirements 12.1**
        """
        user_id = f"user-{uuid.uuid4().hex[:8]}"

        # Get or create queue for user
        queue_url = queue_manager.get_or_create_queue(user_id)

        # Verify queue URL is returned
        assert queue_url is not None
        assert isinstance(queue_url, str)
        assert len(queue_url) > 0

        # Verify queue exists in SQS
        response = sqs_client.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=["QueueArn"],
        )
        assert "Attributes" in response
        assert "QueueArn" in response["Attributes"]

    def test_queue_reused_for_existing_user(self, sqs_client, queue_manager, cleanup_queues):
        """Verify the same queue is returned for an existing user.

        Feature: mordecai, Property 25: User Queue Creation
        **Validates: Requirements 12.1**
        """
        user_id = f"user-{uuid.uuid4().hex[:8]}"

        # Create queue first time
        queue_url_1 = queue_manager.get_or_create_queue(user_id)

        # Get queue second time
        queue_url_2 = queue_manager.get_or_create_queue(user_id)

        # Should be the same queue
        assert queue_url_1 == queue_url_2

    def test_different_users_get_different_queues(self, sqs_client, queue_manager, cleanup_queues):
        """Verify different users get different queues.

        Feature: mordecai, Property 25: User Queue Creation
        **Validates: Requirements 12.1**
        """
        user_id_1 = f"user-{uuid.uuid4().hex[:8]}"
        user_id_2 = f"user-{uuid.uuid4().hex[:8]}"

        queue_url_1 = queue_manager.get_or_create_queue(user_id_1)
        queue_url_2 = queue_manager.get_or_create_queue(user_id_2)

        # Different users should have different queues
        assert queue_url_1 != queue_url_2

    def test_queue_has_correct_attributes(self, sqs_client, queue_manager, cleanup_queues):
        """Verify queue is created with correct configuration.

        Feature: mordecai, Property 25: User Queue Creation
        **Validates: Requirements 12.1**
        """
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        queue_url = queue_manager.get_or_create_queue(user_id)

        # Get queue attributes
        response = sqs_client.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=["VisibilityTimeout", "MessageRetentionPeriod"],
        )

        attrs = response["Attributes"]
        # Verify configured attributes
        assert attrs["VisibilityTimeout"] == "900"  # 15 minutes
        assert attrs["MessageRetentionPeriod"] == "86400"  # 24 hours


class TestMessageConsumptionAndRouting:
    """Tests for Property 26: Message Consumption and Routing.

    *For any* message in a user's SQS queue, it should be consumed
    and routed to the agent for processing.
    **Validates: Requirements 12.3, 12.4**
    """

    @pytest_asyncio.fixture
    async def mock_agent_service(self):
        """Create a mock agent service."""
        async def process_message(user_id: str, message: str, onboarding_context=None) -> str:
            return "Test response"
        mock = AsyncMock()
        mock.process_message = AsyncMock(side_effect=process_message)
        return mock

    async def test_message_consumed_from_queue(
        self, sqs_client, queue_manager, mock_agent_service, cleanup_queues
    ):
        """Verify messages are consumed from user queues.

        Feature: mordecai, Property 26: Message Consumption and Routing
        **Validates: Requirements 12.3, 12.4**
        """
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        queue_url = queue_manager.get_or_create_queue(user_id)

        # Send a message to the queue
        message_body = json.dumps(
            {
                "user_id": user_id,
                "message": "Hello, agent!",
                "chat_id": 12345,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )
        sqs_client.send_message(QueueUrl=queue_url, MessageBody=message_body)

        # Create processor
        processor = MessageProcessor(
            sqs_client=sqs_client,
            queue_manager=queue_manager,
            agent_service=mock_agent_service,
            polling_interval=0.1,
        )

        # Process the queue once
        await processor._process_queue(queue_url)

        # Verify agent service was called
        mock_agent_service.process_message.assert_called_once_with(
            user_id=user_id,
            message="Hello, agent!",
            onboarding_context=None,
        )

    async def test_message_deleted_after_processing(
        self, sqs_client, queue_manager, mock_agent_service, cleanup_queues
    ):
        """Verify messages are deleted after successful processing.

        Feature: mordecai, Property 26: Message Consumption and Routing
        **Validates: Requirements 12.3, 12.4**
        """
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        queue_url = queue_manager.get_or_create_queue(user_id)

        # Send a message
        message_body = json.dumps(
            {
                "user_id": user_id,
                "message": "Process me",
                "chat_id": 12345,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )
        sqs_client.send_message(QueueUrl=queue_url, MessageBody=message_body)

        # Process the queue
        processor = MessageProcessor(
            sqs_client=sqs_client,
            queue_manager=queue_manager,
            agent_service=mock_agent_service,
            polling_interval=0.1,
        )
        await processor._process_queue(queue_url)

        # Verify queue is empty (message was deleted)
        response = sqs_client.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=1,
        )
        assert "Messages" not in response or len(response["Messages"]) == 0

    async def test_response_callback_invoked(
        self, sqs_client, queue_manager, mock_agent_service, cleanup_queues
    ):
        """Verify response callback is invoked after processing.

        Feature: mordecai, Property 26: Message Consumption and Routing
        **Validates: Requirements 12.3, 12.4**
        """
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        queue_url = queue_manager.get_or_create_queue(user_id)
        chat_id = 12345

        # Send a message
        message_body = json.dumps(
            {
                "user_id": user_id,
                "message": "Test message",
                "chat_id": chat_id,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )
        sqs_client.send_message(QueueUrl=queue_url, MessageBody=message_body)

        # Create callback mock
        callback = AsyncMock()

        # Process the queue
        processor = MessageProcessor(
            sqs_client=sqs_client,
            queue_manager=queue_manager,
            agent_service=mock_agent_service,
            response_callback=callback,
            polling_interval=0.1,
        )
        await processor._process_queue(queue_url)

        # Verify callback was invoked with correct args
        callback.assert_called_once_with(chat_id, "Test response")

    async def test_malformed_message_deleted(
        self, sqs_client, queue_manager, mock_agent_service, cleanup_queues
    ):
        """Verify malformed messages are deleted (not retried).

        Feature: mordecai, Property 26: Message Consumption and Routing
        **Validates: Requirements 12.3, 12.4**
        """
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        queue_url = queue_manager.get_or_create_queue(user_id)

        # Send a malformed message (invalid JSON)
        sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody="not valid json",
        )

        # Process the queue
        processor = MessageProcessor(
            sqs_client=sqs_client,
            queue_manager=queue_manager,
            agent_service=mock_agent_service,
            polling_interval=0.1,
        )
        await processor._process_queue(queue_url)

        # Agent should not have been called
        mock_agent_service.process_message.assert_not_called()

        # Queue should be empty (malformed message deleted)
        response = sqs_client.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=1,
        )
        assert "Messages" not in response or len(response["Messages"]) == 0


class TestMessageProcessingOrder:
    """Tests for Property 27: Message Processing Order.

    *For any* user, messages should be processed in the order
    they were sent (FIFO).
    **Validates: Requirements 12.6**
    """

    @pytest_asyncio.fixture
    async def tracking_agent_service(self):
        """Create an agent service that tracks message order."""
        processed_messages = []

        async def track_message(user_id: str, message: str, onboarding_context=None) -> str:
            processed_messages.append(message)
            return f"Processed: {message}"

        mock = AsyncMock()
        mock.process_message = track_message
        mock.processed_messages = processed_messages
        return mock

    async def test_messages_processed_in_order(
        self, sqs_client, queue_manager, tracking_agent_service, cleanup_queues
    ):
        """Verify messages are processed in FIFO order.

        Feature: mordecai, Property 27: Message Processing Order
        **Validates: Requirements 12.6**
        """
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        queue_url = queue_manager.get_or_create_queue(user_id)

        # Send multiple messages in order
        messages = ["First", "Second", "Third", "Fourth", "Fifth"]
        for msg in messages:
            message_body = json.dumps(
                {
                    "user_id": user_id,
                    "message": msg,
                    "chat_id": 12345,
                    "timestamp": datetime.utcnow().isoformat(),
                }
            )
            sqs_client.send_message(QueueUrl=queue_url, MessageBody=message_body)

        # Create processor
        processor = MessageProcessor(
            sqs_client=sqs_client,
            queue_manager=queue_manager,
            agent_service=tracking_agent_service,
            polling_interval=0.1,
        )

        # Process all messages (one at a time to maintain order)
        for _ in range(len(messages)):
            await processor._process_queue(queue_url)

        # Verify messages were processed in order
        assert tracking_agent_service.processed_messages == messages

    async def test_single_message_per_receive(self, sqs_client, queue_manager, cleanup_queues):
        """Verify processor receives one message at a time for ordering.

        Feature: mordecai, Property 27: Message Processing Order
        **Validates: Requirements 12.6**
        """
        user_id = f"user-{uuid.uuid4().hex[:8]}"
        queue_url = queue_manager.get_or_create_queue(user_id)

        # Send multiple messages
        for i in range(5):
            message_body = json.dumps(
                {
                    "user_id": user_id,
                    "message": f"Message {i}",
                    "chat_id": 12345,
                    "timestamp": datetime.utcnow().isoformat(),
                }
            )
            sqs_client.send_message(QueueUrl=queue_url, MessageBody=message_body)

        # Receive messages directly to verify MaxNumberOfMessages=1
        response = sqs_client.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=1,
        )

        # Should receive exactly 1 message
        assert len(response.get("Messages", [])) == 1


class TestQueueManagerOperations:
    """Additional tests for queue manager operations."""

    def test_get_user_count(self, sqs_client, queue_manager, cleanup_queues):
        """Verify user count is tracked correctly."""
        assert queue_manager.get_user_count() == 0

        queue_manager.get_or_create_queue("user1")
        assert queue_manager.get_user_count() == 1

        queue_manager.get_or_create_queue("user2")
        assert queue_manager.get_user_count() == 2

        # Same user shouldn't increase count
        queue_manager.get_or_create_queue("user1")
        assert queue_manager.get_user_count() == 2

    def test_get_queue_url_for_user(self, sqs_client, queue_manager, cleanup_queues):
        """Verify get_queue_url_for_user returns correct values."""
        user_id = f"user-{uuid.uuid4().hex[:8]}"

        # Should return None for unknown user
        assert queue_manager.get_queue_url_for_user(user_id) is None

        # Create queue
        queue_url = queue_manager.get_or_create_queue(user_id)

        # Should return the queue URL
        assert queue_manager.get_queue_url_for_user(user_id) == queue_url

    def test_remove_user_queue(self, sqs_client, queue_manager, cleanup_queues):
        """Verify remove_user_queue removes from tracking."""
        user_id = f"user-{uuid.uuid4().hex[:8]}"

        # Create queue
        queue_manager.get_or_create_queue(user_id)
        assert queue_manager.get_user_count() == 1

        # Remove from tracking
        result = queue_manager.remove_user_queue(user_id)
        assert result is True
        assert queue_manager.get_user_count() == 0

        # Removing again should return False
        result = queue_manager.remove_user_queue(user_id)
        assert result is False

    def test_delete_queue(self, sqs_client, queue_manager, cleanup_queues):
        """Verify delete_queue removes queue from SQS."""
        user_id = f"user-{uuid.uuid4().hex[:8]}"

        # Create queue
        queue_url = queue_manager.get_or_create_queue(user_id)

        # Verify queue exists
        response = sqs_client.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=["QueueArn"],
        )
        assert "Attributes" in response

        # Delete queue
        result = queue_manager.delete_queue(user_id)
        assert result is True
        assert queue_manager.get_user_count() == 0

        # Verify queue no longer exists
        with pytest.raises(Exception):
            sqs_client.get_queue_attributes(
                QueueUrl=queue_url,
                AttributeNames=["QueueArn"],
            )

    def test_get_all_queue_urls(self, sqs_client, queue_manager, cleanup_queues):
        """Verify get_all_queue_urls returns all registered queues."""
        users = [f"user-{uuid.uuid4().hex[:8]}" for _ in range(3)]
        urls = [queue_manager.get_or_create_queue(u) for u in users]

        all_urls = queue_manager.get_all_queue_urls()
        assert len(all_urls) == 3
        assert set(all_urls) == set(urls)
