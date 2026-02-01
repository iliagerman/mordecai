from __future__ import annotations

import re
from typing import Any


def extract_response_text(result: Any) -> str:
    """Extract a user-facing response text from a Strands agent result."""

    if hasattr(result, "message") and getattr(result, "message"):
        message = getattr(result, "message")
        try:
            content = message.get("content", [])  # type: ignore[call-arg]
        except Exception:
            content = []

        text_parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if "text" not in block:
                continue

            text = str(block["text"])
            # Remove thinking blocks (content between <thinking> tags)
            text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL)
            # Collapse excessive blank lines
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            if text:
                text_parts.append(text)

        if text_parts:
            # Prefer the last block to avoid duplicate intermediate drafts.
            return text_parts[-1]

    return str(result)
