"""Integration tests for multi-user isolation.

Tests verify:
- Property 13: Session Isolation Between Users
- Property 22: Concurrent User Support

Requirements: 9.1, 9.2, 9.3
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


class TestSessionIsolationBetweenUsers:
    """Tests for Property 13: Session Isolation Between Users.

    *For any* two distinct users, their sessions should be completely
    isolated—messages and memory from one user should not appear in
    the other's session.

    **Validates: Requirements 5.2, 9.2**
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
        prefix = f"iso-{uuid.uuid4().hex[:8]}-"
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

    def test_different_users_get_different_queues(self, sqs_client, queue_manager, cleanup_queues):
        """Verify different users get isolated queues.

        Feature: mordecai, Property 13: Session Isolation Between Users
        **Validates: Requirements 5.2, 9.2**
        """
        user_id_1 = f"user-{uuid.uuid4().hex[:8]}"
        user_id_2 = f"user-{uuid.uuid4().hex[:8]}"

        queue_url_1 = queue_manager.get_or_create_queue(user_id_1)
        queue_url_2 = queue_manager.get_or_create_queue(user_id_2)

        # Different users should have different queues
        assert queue_url_1 != queue_url_2

    def test_messages_isolated_between_user_queues(self, sqs_client, queue_manager, cleanup_queues):
        """Verify messages in one user's queue don't appear in another's.

        Feature: mordecai, Property 13: Session Isolation Between Users
        **Validates: Requirements 5.2, 9.2**
        """
        user_id_1 = f"user-{uuid.uuid4().hex[:8]}"
        user_id_2 = f"user-{uuid.uuid4().hex[:8]}"

        queue_url_1 = queue_manager.get_or_create_queue(user_id_1)
        queue_url_2 = queue_manager.get_or_create_queue(user_id_2)

        # Send message to user 1's queue
        message_1 = "Secret message for user 1"
        sqs_client.send_message(
            QueueUrl=queue_url_1,
            MessageBody=json.dumps(
                {
                    "user_id": user_id_1,
                    "message": message_1,
                    "chat_id": 1001,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            ),
        )

        # Send message to user 2's queue
        message_2 = "Secret message for user 2"
        sqs_client.send_message(
            QueueUrl=queue_url_2,
            MessageBody=json.dumps(
                {
                    "user_id": user_id_2,
                    "message": message_2,
                    "chat_id": 1002,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            ),
        )

        # Receive from user 1's queue
        response_1 = sqs_client.receive_message(
            QueueUrl=queue_url_1,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=1,
        )
        messages_1 = response_1.get("Messages", [])
        assert len(messages_1) == 1
        body_1 = json.loads(messages_1[0]["Body"])
        assert body_1["message"] == message_1
        assert body_1["user_id"] == user_id_1

        # Receive from user 2's queue
        response_2 = sqs_client.receive_message(
            QueueUrl=queue_url_2,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=1,
        )
        messages_2 = response_2.get("Messages", [])
        assert len(messages_2) == 1
        body_2 = json.loads(messages_2[0]["Body"])
        assert body_2["message"] == message_2
        assert body_2["user_id"] == user_id_2

    @pytest.mark.asyncio
    async def test_agent_service_session_isolation(self, temp_dir):
        """Verify AgentService maintains isolated sessions per user.

        Feature: mordecai, Property 13: Session Isolation Between Users
        **Validates: Requirements 5.2, 9.2**
        """
        config = AgentConfig(
            model_provider=ModelProvider.BEDROCK,
            bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            telegram_bot_token="test-token",
            session_storage_dir=temp_dir,
            skills_base_dir=temp_dir,
        )

        # Mock the model creation to avoid actual API calls
        with patch.object(AgentService, "_create_model") as mock_create_model:
            mock_model = MagicMock()
            mock_create_model.return_value = mock_model

            service = AgentService(config)

            user_id_1 = f"user-{uuid.uuid4().hex[:8]}"
            user_id_2 = f"user-{uuid.uuid4().hex[:8]}"

            # Get agents for both users
            with patch("strands.Agent") as mock_agent_class:
                mock_agent_1 = MagicMock()
                mock_agent_2 = MagicMock()
                mock_agent_class.side_effect = [mock_agent_1, mock_agent_2]

                agent_1 = service.get_or_create_agent(user_id_1)
                agent_2 = service.get_or_create_agent(user_id_2)

                # Verify different agent instances
                assert agent_1 is not agent_2

                # Verify agents are cached per user
                agent_1_again = service.get_or_create_agent(user_id_1)
                assert agent_1_again is agent_1

    @pytest.mark.asyncio
    async def test_message_processor_routes_to_correct_user(
        self, sqs_client, queue_manager, cleanup_queues
    ):
        """Verify MessageProcessor routes messages to correct user's agent.

        Feature: mordecai, Property 13: Session Isolation Between Users
        **Validates: Requirements 5.2, 9.2**
        """
        user_id_1 = f"user-{uuid.uuid4().hex[:8]}"
        user_id_2 = f"user-{uuid.uuid4().hex[:8]}"

        # Track which user_id was passed to process_message
        processed_users = []

        async def track_process(user_id: str, message: str, onboarding_context=None) -> str:
            processed_users.append(user_id)
            return f"Response for {user_id}"

        mock_agent_service = AsyncMock()
        mock_agent_service.process_message = track_process

        # Create queues and send messages
        queue_url_1 = queue_manager.get_or_create_queue(user_id_1)
        queue_url_2 = queue_manager.get_or_create_queue(user_id_2)

        sqs_client.send_message(
            QueueUrl=queue_url_1,
            MessageBody=json.dumps(
                {
                    "user_id": user_id_1,
                    "message": "Message from user 1",
                    "chat_id": 1001,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            ),
        )

        sqs_client.send_message(
            QueueUrl=queue_url_2,
            MessageBody=json.dumps(
                {
                    "user_id": user_id_2,
                    "message": "Message from user 2",
                    "chat_id": 1002,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            ),
        )

        # Create processor
        processor = MessageProcessor(
            sqs_client=sqs_client,
            queue_manager=queue_manager,
            agent_service=mock_agent_service,
            polling_interval=0.1,
        )

        # Process both queues
        await processor._process_queue(queue_url_1)
        await processor._process_queue(queue_url_2)

        # Verify each user's message was routed correctly
        assert user_id_1 in processed_users
        assert user_id_2 in processed_users
        assert len(processed_users) == 2

    @pytest.mark.parametrize("num_users", [2, 3, 4])
    def test_property_queue_isolation(self, num_users: int, sqs_client, cleanup_queues):
        """Property test: Each user gets a unique, isolated queue.

        Feature: mordecai, Property 13: Session Isolation Between Users
        **Validates: Requirements 5.2, 9.2**
        """
        prefix = f"prop-{uuid.uuid4().hex[:6]}-"
        queue_manager = SQSQueueManager(sqs_client, queue_prefix=prefix)

        try:
            # Generate user IDs
            user_ids = [f"user-{uuid.uuid4().hex[:8]}" for _ in range(num_users)]

            queue_urls = []
            for user_id in user_ids:
                queue_url = queue_manager.get_or_create_queue(user_id)
                queue_urls.append(queue_url)

            # Property: All queue URLs should be unique
            assert len(set(queue_urls)) == len(user_ids)

            # Property: Each user should get the same queue on repeated calls
            for user_id in user_ids:
                url_1 = queue_manager.get_or_create_queue(user_id)
                url_2 = queue_manager.get_or_create_queue(user_id)
                assert url_1 == url_2

        finally:
            # Cleanup
            for url in queue_manager.get_all_queue_urls():
                try:
                    sqs_client.delete_queue(QueueUrl=url)
                except Exception:
                    pass


class TestConcurrentUserSupport:
    """Tests for Property 22: Concurrent User Support.

    *For any* number of concurrent users (within system limits), all
    should be able to interact with the agent independently.

    **Validates: Requirements 9.1, 9.3**
    """

    @pytest.fixture
    def sqs_client(self):
        """Create a fresh SQS client for each test."""
        return get_localstack_sqs_client()

    @pytest.fixture
    def queue_manager(self, sqs_client) -> SQSQueueManager:
        """Create a queue manager with unique prefix for test isolation."""
        prefix = f"conc-{uuid.uuid4().hex[:8]}-"
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
    async def test_concurrent_queue_creation(self, sqs_client, queue_manager, cleanup_queues):
        """Verify multiple users can create queues concurrently.

        Feature: mordecai, Property 22: Concurrent User Support
        **Validates: Requirements 9.1, 9.3**
        """
        num_users = 5
        user_ids = [f"user-{uuid.uuid4().hex[:8]}" for _ in range(num_users)]

        # Create queues concurrently
        async def create_queue(user_id: str) -> str:
            return queue_manager.get_or_create_queue(user_id)

        tasks = [create_queue(uid) for uid in user_ids]
        queue_urls = await asyncio.gather(*tasks)

        # Verify all queues were created
        assert len(queue_urls) == num_users
        assert len(set(queue_urls)) == num_users  # All unique

    @pytest.mark.asyncio
    async def test_concurrent_message_processing(self, sqs_client, queue_manager, cleanup_queues):
        """Verify messages from multiple users can be processed concurrently.

        Feature: mordecai, Property 22: Concurrent User Support
        **Validates: Requirements 9.1, 9.3**
        """
        num_users = 3
        users = [
            {"user_id": f"user-{uuid.uuid4().hex[:8]}", "chat_id": 1000 + i}
            for i in range(num_users)
        ]

        # Track processed messages
        processed = []
        lock = asyncio.Lock()

        async def track_process(user_id: str, message: str, onboarding_context=None) -> str:
            async with lock:
                processed.append({"user_id": user_id, "message": message})
            return f"Response for {user_id}"

        mock_agent_service = AsyncMock()
        mock_agent_service.process_message = track_process

        # Create queues and send messages
        for user in users:
            queue_url = queue_manager.get_or_create_queue(user["user_id"])
            sqs_client.send_message(
                QueueUrl=queue_url,
                MessageBody=json.dumps(
                    {
                        "user_id": user["user_id"],
                        "message": f"Message from {user['user_id']}",
                        "chat_id": user["chat_id"],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                ),
            )

        # Create processor
        processor = MessageProcessor(
            sqs_client=sqs_client,
            queue_manager=queue_manager,
            agent_service=mock_agent_service,
            polling_interval=0.1,
        )

        # Process all queues concurrently
        queue_urls = queue_manager.get_all_queue_urls()
        tasks = [processor._process_queue(url) for url in queue_urls]
        await asyncio.gather(*tasks)

        # Verify all messages were processed
        assert len(processed) == num_users
        processed_user_ids = {p["user_id"] for p in processed}
        expected_user_ids = {u["user_id"] for u in users}
        assert processed_user_ids == expected_user_ids

    @pytest.mark.asyncio
    async def test_concurrent_responses_delivered_correctly(
        self, sqs_client, queue_manager, cleanup_queues
    ):
        """Verify responses are delivered to correct users concurrently.

        Feature: mordecai, Property 22: Concurrent User Support
        **Validates: Requirements 9.1, 9.3**
        """
        num_users = 4
        users = [
            {"user_id": f"user-{uuid.uuid4().hex[:8]}", "chat_id": 2000 + i}
            for i in range(num_users)
        ]

        # Track responses
        responses = []
        lock = asyncio.Lock()

        async def capture_response(chat_id: int, response: str):
            async with lock:
                responses.append({"chat_id": chat_id, "response": response})

        async def process_message(user_id: str, message: str, onboarding_context=None) -> str:
            # Simulate some processing time
            await asyncio.sleep(0.01)
            return f"Response for {user_id}"

        mock_agent_service = AsyncMock()
        mock_agent_service.process_message = process_message

        # Create queues and send messages
        for user in users:
            queue_url = queue_manager.get_or_create_queue(user["user_id"])
            sqs_client.send_message(
                QueueUrl=queue_url,
                MessageBody=json.dumps(
                    {
                        "user_id": user["user_id"],
                        "message": f"Message from {user['user_id']}",
                        "chat_id": user["chat_id"],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                ),
            )

        # Create processor with response callback
        processor = MessageProcessor(
            sqs_client=sqs_client,
            queue_manager=queue_manager,
            agent_service=mock_agent_service,
            response_callback=capture_response,
            polling_interval=0.1,
        )

        # Process all queues concurrently
        queue_urls = queue_manager.get_all_queue_urls()
        tasks = [processor._process_queue(url) for url in queue_urls]
        await asyncio.gather(*tasks)

        # Verify all responses were delivered
        assert len(responses) == num_users
        response_chat_ids = {r["chat_id"] for r in responses}
        expected_chat_ids = {u["chat_id"] for u in users}
        assert response_chat_ids == expected_chat_ids

        # Verify each response contains correct user reference
        for response in responses:
            # Find the user with this chat_id
            user = next(u for u in users if u["chat_id"] == response["chat_id"])
            assert user["user_id"] in response["response"]

    @pytest.mark.asyncio
    async def test_independent_user_interactions(self, sqs_client, queue_manager, cleanup_queues):
        """Verify users can interact independently without interference.

        Feature: mordecai, Property 22: Concurrent User Support
        **Validates: Requirements 9.1, 9.3**
        """
        user_1 = {"user_id": f"user-{uuid.uuid4().hex[:8]}", "chat_id": 3001}
        user_2 = {"user_id": f"user-{uuid.uuid4().hex[:8]}", "chat_id": 3002}

        # Track processing order and results
        processing_log = []
        lock = asyncio.Lock()

        async def track_process(user_id: str, message: str, onboarding_context=None) -> str:
            async with lock:
                processing_log.append(
                    {
                        "user_id": user_id,
                        "message": message,
                        "time": datetime.now(timezone.utc).isoformat(),
                    }
                )
            return f"Processed: {message}"

        mock_agent_service = AsyncMock()
        mock_agent_service.process_message = track_process

        # Create queues
        queue_url_1 = queue_manager.get_or_create_queue(user_1["user_id"])
        queue_url_2 = queue_manager.get_or_create_queue(user_2["user_id"])

        # Send multiple messages for each user
        num_messages = 3
        for i in range(num_messages):
            sqs_client.send_message(
                QueueUrl=queue_url_1,
                MessageBody=json.dumps(
                    {
                        "user_id": user_1["user_id"],
                        "message": f"User1 message {i}",
                        "chat_id": user_1["chat_id"],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                ),
            )
            sqs_client.send_message(
                QueueUrl=queue_url_2,
                MessageBody=json.dumps(
                    {
                        "user_id": user_2["user_id"],
                        "message": f"User2 message {i}",
                        "chat_id": user_2["chat_id"],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                ),
            )

        # Create processor
        processor = MessageProcessor(
            sqs_client=sqs_client,
            queue_manager=queue_manager,
            agent_service=mock_agent_service,
            polling_interval=0.1,
        )

        # Process messages from each queue - process each queue's messages
        for _ in range(num_messages):
            await processor._process_queue(queue_url_1)
        for _ in range(num_messages):
            await processor._process_queue(queue_url_2)

        # Verify all messages were processed
        user_1_messages = [log for log in processing_log if log["user_id"] == user_1["user_id"]]
        user_2_messages = [log for log in processing_log if log["user_id"] == user_2["user_id"]]

        assert len(user_1_messages) == num_messages
        assert len(user_2_messages) == num_messages

        # Verify message content is correct for each user
        for i, log in enumerate(user_1_messages):
            assert f"User1 message {i}" in log["message"]
        for i, log in enumerate(user_2_messages):
            assert f"User2 message {i}" in log["message"]

    @pytest.mark.asyncio
    async def test_property_concurrent_user_support(
        self, sqs_client, queue_manager, cleanup_queues
    ):
        """Property test: Any number of concurrent users can interact independently.

        Feature: mordecai, Property 22: Concurrent User Support
        **Validates: Requirements 9.1, 9.3**
        """
        # Test with varying numbers of users and messages
        test_cases = [
            (2, 1),
            (3, 2),
        ]

        for num_users, messages_per_user in test_cases:
            # Track processed messages
            processed = []
            lock = asyncio.Lock()

            async def track_process(user_id: str, message: str, onboarding_context=None) -> str:
                async with lock:
                    processed.append({"user_id": user_id, "message": message})
                return f"Response for {user_id}"

            mock_agent_service = AsyncMock()
            mock_agent_service.process_message = track_process

            # Create a new queue manager for this test case
            test_prefix = f"prop-conc-{uuid.uuid4().hex[:6]}-"
            test_queue_manager = SQSQueueManager(sqs_client, queue_prefix=test_prefix)

            try:
                # Create users
                users = [
                    {"user_id": f"user-{uuid.uuid4().hex[:8]}", "chat_id": 4000 + i}
                    for i in range(num_users)
                ]

                # Create queues and send messages
                queue_urls = []
                for user in users:
                    queue_url = test_queue_manager.get_or_create_queue(user["user_id"])
                    queue_urls.append(queue_url)
                    for j in range(messages_per_user):
                        sqs_client.send_message(
                            QueueUrl=queue_url,
                            MessageBody=json.dumps(
                                {
                                    "user_id": user["user_id"],
                                    "message": f"Message {j} from {user['user_id']}",
                                    "chat_id": user["chat_id"],
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                }
                            ),
                        )

                # Create processor
                processor = MessageProcessor(
                    sqs_client=sqs_client,
                    queue_manager=test_queue_manager,
                    agent_service=mock_agent_service,
                    polling_interval=0.1,
                )

                # Process all messages - process each queue's messages
                for queue_url in queue_urls:
                    for _ in range(messages_per_user):
                        await processor._process_queue(queue_url)

                # Property: All messages should be processed
                total_messages = num_users * messages_per_user
                assert len(processed) == total_messages

                # Property: Each user should have correct number of messages
                for user in users:
                    user_messages = [p for p in processed if p["user_id"] == user["user_id"]]
                    assert len(user_messages) == messages_per_user

            finally:
                # Cleanup
                for url in test_queue_manager.get_all_queue_urls():
                    try:
                        sqs_client.delete_queue(QueueUrl=url)
                    except Exception:
                        pass
