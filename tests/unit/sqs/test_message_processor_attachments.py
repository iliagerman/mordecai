import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.agent import AttachmentInfo
from app.sqs.message_processor import MessageProcessor


@pytest.mark.asyncio
async def test_worker_parses_attachment_dicts_into_models() -> None:
    """Regression: attachments from SQS arrive as dicts, but agent expects models.

    If we pass raw dicts through, downstream code will crash with:
    'dict' object has no attribute 'file_name'.
    """

    sqs_client = MagicMock()
    sqs_client.delete_message.return_value = {}
    sqs_client.change_message_visibility.return_value = {}

    queue_manager = MagicMock()

    async def process_message_with_attachments(
        *, user_id: str, message: str, attachments, onboarding_context=None
    ):  # type: ignore[no-untyped-def]
        assert user_id == "u1"
        assert message == "hello"
        assert isinstance(attachments, list)
        assert attachments, "expected at least one attachment"
        assert isinstance(attachments[0], AttachmentInfo)
        assert attachments[0].file_name == "voice_uniq.ogg"
        return "ok"

    agent_service = MagicMock()
    agent_service.process_message_with_attachments = AsyncMock(
        side_effect=process_message_with_attachments
    )
    agent_service.process_message = AsyncMock(return_value="ok")

    processor = MessageProcessor(
        sqs_client=sqs_client,
        queue_manager=queue_manager,
        agent_service=agent_service,
        polling_interval=0.01,
    )

    body = {
        "user_id": "u1",
        "message": "hello",
        "chat_id": 1,
        "timestamp": datetime.now(UTC).isoformat(),
        "attachments": [
            {
                "file_id": "fid",
                "file_name": "voice_uniq.ogg",
                "file_path": "/tmp/voice_uniq.ogg",
                "mime_type": "audio/ogg",
                "file_size": 1234,
                "is_image": False,
            }
        ],
    }

    message = {
        "MessageId": "m1",
        "ReceiptHandle": "rh-m1",
        "Body": json.dumps(body),
    }

    res = await processor._handle_message("q://u1", message, body=body)

    assert res == "ok"
    agent_service.process_message_with_attachments.assert_awaited_once()
    assert sqs_client.delete_message.call_count == 1
