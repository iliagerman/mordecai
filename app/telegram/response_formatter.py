"""Telegram response formatting utilities.

This module handles formatting responses for Telegram, including:
- Markdown to HTML conversion
- Table formatting
- Code block handling
"""

from __future__ import annotations

import logging
import re
from html import escape

logger = logging.getLogger(__name__)


class TelegramResponseFormatter:
    """Handles formatting responses for Telegram.

    Converts standard markdown to Telegram's HTML format, which is
    more reliable than MarkdownV2 for complex formatting.
    """

    def format_for_html(self, text: str) -> str:
        """Convert standard markdown to Telegram HTML format.

        HTML is more reliable than MarkdownV2 for complex formatting like tables.

        Args:
            text: Text with standard markdown.

        Returns:
            Telegram HTML formatted text.
        """
        # Extract code blocks BEFORE escaping (preserve their content)
        code_blocks = []

        def save_code_block(match: re.Match) -> str:
            code_blocks.append(match.group(1))
            return f"__CODE_BLOCK_{len(code_blocks) - 1}__"

        result = re.sub(r"```(?:\w+)?\n?(.*?)```", save_code_block, text, flags=re.DOTALL)

        # Convert markdown tables into a human-friendly list BEFORE HTML escaping.
        # Telegram does not render markdown pipe tables reliably.
        def convert_table(match: re.Match) -> str:
            raw_lines = [ln.strip() for ln in match.group(0).strip().split("\n") if ln.strip()]
            if len(raw_lines) < 2:
                return match.group(0)

            # Drop separator lines (contains only |, -, :, and spaces)
            lines = [ln for ln in raw_lines if not re.match(r"^[\|\-\s:]+$", ln)]
            if not lines:
                return ""

            # Parse header + rows
            header = [c.strip() for c in lines[0].strip("|").split("|")]
            rows = [[c.strip() for c in ln.strip("|").split("|")] for ln in lines[1:]]

            out_lines: list[str] = []
            for idx, row in enumerate(rows, start=1):
                # Pad/truncate to header length
                if len(row) < len(header):
                    row = row + [""] * (len(header) - len(row))
                if len(row) > len(header) and header:
                    row = row[: len(header)]

                if header and any(h for h in header):
                    parts: list[str] = []
                    for h, v in zip(header, row, strict=False):
                        h = (h or "").strip()
                        v = (v or "").strip()
                        if not h and not v:
                            continue
                        if h and v:
                            parts.append(f"{h}: {v}")
                        elif v:
                            parts.append(v)
                    line = f"{idx}. " + "; ".join(parts)
                else:
                    # Fallback: no header found
                    parts = [c for c in row if c]
                    line = f"{idx}. " + " ".join(parts)

                out_lines.append(line.strip())

            return "\n".join(out_lines)

        # Match markdown tables (consecutive lines starting with |)
        result = re.sub(r"(?:(?:^\|.+)\n?)+", convert_table, result, flags=re.MULTILINE)

        # Now escape HTML special characters
        result = escape(result)

        # Convert headers to bold
        result = re.sub(r"^### (.+)$", r"<b>\1</b>", result, flags=re.MULTILINE)
        result = re.sub(r"^## (.+)$", r"<b>\1</b>", result, flags=re.MULTILINE)
        result = re.sub(r"^# (.+)$", r"<b>\1</b>", result, flags=re.MULTILINE)

        # Convert **bold** to <b>bold</b>
        result = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", result)

        # Convert *italic* to <i>italic</i>
        result = re.sub(r"\*(.+?)\*", r"<i>\1</i>", result)

        # Convert `code` to <code>code</code>
        result = re.sub(r"`([^`]+)`", r"<code>\1</code>", result)

        # Restore code blocks as <pre> (escape their content now)
        for i, code in enumerate(code_blocks):
            result = result.replace(f"__CODE_BLOCK_{i}__", f"<pre>{escape(code)}</pre>")

        # Convert [text](url) to <a href="url">text</a>
        result = re.sub(r"\[([^\]]+)\]\(([^\)]+)\)", r'<a href="\2">\1</a>', result)

        return result

    def format_for_markdown_v2(self, text: str) -> str:
        """Convert standard markdown to Telegram MarkdownV2 format.

        Telegram MarkdownV2 requires escaping special characters.

        Args:
            text: Text with standard markdown.

        Returns:
            Telegram MarkdownV2 formatted text.
        """
        # Characters that need escaping in MarkdownV2
        # (except those used for formatting: * _ ` [ ])
        special_chars = r"([.!#()\-+={}|>])"

        # First, escape special characters
        result = re.sub(special_chars, r"\\\1", text)

        # Convert # headers to bold (Telegram doesn't support headers)
        result = re.sub(r"^\\\#\\\#\\\# (.+)$", r"*\1*", result, flags=re.MULTILINE)
        result = re.sub(r"^\\\#\\\# (.+)$", r"*\1*", result, flags=re.MULTILINE)
        result = re.sub(r"^\\\# (.+)$", r"*\1*", result, flags=re.MULTILINE)

        return result

    def get_severity_emoji(self, severity: str) -> str:
        """Get emoji for log severity level.

        Args:
            severity: Log severity level.

        Returns:
            Emoji string for the severity.
        """
        from app.enums import LogSeverity

        # Handle both string and enum inputs
        severity_str = str(severity).upper() if not isinstance(severity, str) else severity

        match severity_str:
            case "DEBUG" | LogSeverity.DEBUG:
                return "🔍"
            case "INFO" | LogSeverity.INFO:
                return "ℹ️"
            case "WARNING" | LogSeverity.WARNING:
                return "⚠️"
            case "ERROR" | LogSeverity.ERROR:
                return "❌"
            case _:
                return "📝"
