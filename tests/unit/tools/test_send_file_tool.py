"""Unit tests for the send_file tool.

These tests cover a subtle but critical behavior:
- The Strands agent is invoked via asyncio.to_thread()
- The send_file tool runs inside that background thread
- We still need pending files queued by the tool to be visible in the parent
    asyncio task so the message processor can actually send them.

Implementation note:
We key the pending queue off the identity of the per-message send callback.
"""

from __future__ import annotations

import asyncio

import pytest

from app.tools import send_file as send_file_tool


@pytest.mark.asyncio
async def test_send_file_pending_queue_visible_across_to_thread(tmp_path) -> None:
    # Ensure a clean state for this test.
    send_file_tool.clear_send_callbacks()

    async def _noop_send(_path: str, _caption: str | None) -> bool:
        return True

    send_file_tool.set_send_callbacks(_noop_send, _noop_send)

    test_file = tmp_path / "fox.png"
    # Not a real PNG; contents don't matter for the tool.
    test_file.write_bytes(b"fake")

    tool_invocation = {
        "toolUseId": "tool-1",
        "input": {"file_path": str(test_file), "caption": "hi"},
    }

    # Simulate the Strands agent invoking tools inside asyncio.to_thread().
    result = await asyncio.to_thread(send_file_tool.send_file, tool_invocation)
    assert result["status"] == "success"

    pending = send_file_tool.get_pending_files()
    assert len(pending) == 1
    assert pending[0]["path"] == str(test_file)
    assert pending[0]["caption"] == "hi"
    assert pending[0]["is_image"] is True

    # get_pending_files clears the queue.
    assert send_file_tool.get_pending_files() == []
