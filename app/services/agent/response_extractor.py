from __future__ import annotations

import re
from typing import Any


def extract_response_text(result: Any) -> str:
    """Extract a user-facing response text from a Strands agent result."""

    try:
        message = result.message  # type: ignore[attr-defined]
    except Exception:
        message = None

    if message:
        # Strands result.message may be dict-like or a model with `.content`.
        content: object = []
        if isinstance(message, dict):
            content = message.get("content", [])
        else:
            try:
                content = message.content  # type: ignore[attr-defined]
            except Exception:
                content = []

        # Some models return plain string content.
        if isinstance(content, str):
            text = re.sub(r"<thinking>.*?</thinking>", "", content, flags=re.DOTALL)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            return text or str(result)

        text_parts: list[str] = []
        if isinstance(content, list):
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
