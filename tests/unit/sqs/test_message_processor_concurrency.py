import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sqs.message_processor import MessageProcessor


def _make_sqs_message(*, user_id: str, chat_id: int, message: str, message_id: str) -> dict:
    body = {
        "user_id": user_id,
        "message": message,
        "chat_id": chat_id,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    return {
        "MessageId": message_id,
        "ReceiptHandle": f"rh-{message_id}",
        "Body": json.dumps(body),
    }


@pytest.mark.asyncio
async def test_long_running_message_does_not_block_other_queues() -> None:
    """A long-running message for user A should not prevent user B from being processed."""

    # Queue manager returns two queues.
    queue_manager = MagicMock()
    queue_manager.get_all_queue_urls.return_value = ["q://u1", "q://u2"]

    # SQS client returns one message per queue (then empty).
    receive_counts: dict[str, int] = {"q://u1": 0, "q://u2": 0}

    def receive_message(*, QueueUrl: str, **kwargs) -> dict:  # noqa: N803 (boto-style kwargs)
        receive_counts[QueueUrl] += 1
        if QueueUrl == "q://u1" and receive_counts[QueueUrl] == 1:
            return {
                "Messages": [
                    _make_sqs_message(user_id="u1", chat_id=1, message="A", message_id="m1")
                ]
            }
        if QueueUrl == "q://u2" and receive_counts[QueueUrl] == 1:
            return {
                "Messages": [
                    _make_sqs_message(user_id="u2", chat_id=2, message="B", message_id="m2")
                ]
            }
        return {"Messages": []}

    sqs_client = MagicMock()
    sqs_client.receive_message.side_effect = receive_message
    sqs_client.delete_message.return_value = {}
    sqs_client.change_message_visibility.return_value = {}

    # Agent service blocks for u1 but is fast for u2.
    started_u1 = asyncio.Event()
    allow_u1_finish = asyncio.Event()
    processed_u2 = asyncio.Event()

    async def process_message(*, user_id: str, message: str, onboarding_context=None) -> str:  # type: ignore[no-untyped-def]
        if user_id == "u1":
            started_u1.set()
            await allow_u1_finish.wait()
            return "resp-u1"
        processed_u2.set()
        return "resp-u2"

    agent_service = MagicMock()
    agent_service.process_message = AsyncMock(side_effect=process_message)

    processor = MessageProcessor(
        sqs_client=sqs_client,
        queue_manager=queue_manager,
        agent_service=agent_service,
        config=None,  # No config, uses default max_concurrent_per_user=5
        polling_interval=0.01,
        max_prefetch_per_queue=2,
        max_inflight_total=10,
    )

    task = processor.start_background()

    # Wait until u1 starts, then verify u2 completes without waiting for u1.
    await asyncio.wait_for(started_u1.wait(), timeout=1.0)
    await asyncio.wait_for(processed_u2.wait(), timeout=1.0)

    # Clean up.
    allow_u1_finish.set()
    await processor.stop()
    task.cancel()


@pytest.mark.asyncio
async def test_same_queue_allows_parallel_processing() -> None:
    """With parallel processing enabled, multiple messages from the same user run concurrently."""

    queue_manager = MagicMock()
    queue_manager.get_all_queue_urls.return_value = ["q://u1"]

    messages = [
        _make_sqs_message(user_id="u1", chat_id=1, message="first", message_id="m1"),
        _make_sqs_message(user_id="u1", chat_id=1, message="second", message_id="m2"),
    ]

    def receive_message(*, QueueUrl: str, **kwargs) -> dict:  # noqa: N803
        if messages:
            return {"Messages": [messages.pop(0)]}
        return {"Messages": []}

    sqs_client = MagicMock()
    sqs_client.receive_message.side_effect = receive_message
    sqs_client.delete_message.return_value = {}
    sqs_client.change_message_visibility.return_value = {}

    started_first = asyncio.Event()
    started_second = asyncio.Event()
    allow_first_finish = asyncio.Event()
    allow_second_finish = asyncio.Event()

    async def process_message(*, user_id: str, message: str, onboarding_context=None) -> str:  # type: ignore[no-untyped-def]
        if message == "first":
            started_first.set()
            await allow_first_finish.wait()
        elif message == "second":
            started_second.set()
            await allow_second_finish.wait()
        return "ok"

    agent_service = MagicMock()
    agent_service.process_message = AsyncMock(side_effect=process_message)

    processor = MessageProcessor(
        sqs_client=sqs_client,
        queue_manager=queue_manager,
        agent_service=agent_service,
        config=None,  # Uses default max_concurrent_per_user=5
        polling_interval=0.01,
        max_prefetch_per_queue=2,
        max_inflight_total=10,
    )

    task = processor.start_background()

    # Both messages should start processing in parallel
    await asyncio.wait_for(started_first.wait(), timeout=1.0)
    await asyncio.wait_for(started_second.wait(), timeout=1.0)

    # Cleanup.
    allow_first_finish.set()
    allow_second_finish.set()
    await processor.stop()
    task.cancel()


@pytest.mark.asyncio
async def test_semaphore_at_capacity_sends_busy_ack() -> None:
    """When the user's semaphore is at capacity, we send a busy acknowledgment."""

    # Create a mock config with max_concurrent_tasks_per_user=1
    config = MagicMock()
    config.max_concurrent_tasks_per_user = 1

    queue_manager = MagicMock()
    queue_manager.get_all_queue_urls.return_value = ["q://u1"]

    messages = [
        _make_sqs_message(user_id="u1", chat_id=1, message="first", message_id="m1"),
        _make_sqs_message(user_id="u1", chat_id=1, message="second", message_id="m2"),
    ]

    def receive_message(*, QueueUrl: str, **kwargs) -> dict:  # noqa: N803
        if messages:
            return {"Messages": [messages.pop(0)]}
        return {"Messages": []}

    sqs_client = MagicMock()
    sqs_client.receive_message.side_effect = receive_message
    sqs_client.delete_message.return_value = {}
    sqs_client.change_message_visibility.return_value = {}

    started_first = asyncio.Event()
    allow_first_finish = asyncio.Event()

    async def process_message(*, user_id: str, message: str, onboarding_context=None) -> str:  # type: ignore[no-untyped-def]
        if message == "first":
            started_first.set()
            await allow_first_finish.wait()
        return "ok"

    agent_service = MagicMock()
    agent_service.process_message = AsyncMock(side_effect=process_message)

    response_callback = AsyncMock()

    processor = MessageProcessor(
        sqs_client=sqs_client,
        queue_manager=queue_manager,
        agent_service=agent_service,
        config=config,  # max_concurrent_tasks_per_user=1 forces sequential behavior
        response_callback=response_callback,
        polling_interval=0.01,
        max_prefetch_per_queue=2,
        max_inflight_total=10,
    )

    task = processor.start_background()

    # Ensure the first message is actively processing (semaphore held), then allow the poller to prefetch the second.
    await asyncio.wait_for(started_first.wait(), timeout=1.0)
    await asyncio.sleep(0.05)

    # We should have sent an "I'm still working" ack for the second message
    # because the semaphore (capacity=1) is at capacity.
    assert response_callback.await_count >= 1
    assert any(
        (call.args and "still working" in str(call.args[1]).lower())
        for call in response_callback.await_args_list
    )

    # Cleanup.
    allow_first_finish.set()
    await processor.stop()
    task.cancel()
