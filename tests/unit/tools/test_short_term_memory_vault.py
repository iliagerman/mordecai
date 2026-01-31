"""Unit tests for short-term memory vault helpers."""

import shutil
import tempfile
from pathlib import Path

from app.tools.short_term_memory_vault import (
    append_memory,
    parse_short_term_memories,
    read_parsed,
)


def test_parse_short_term_memories_supports_multiple_formats():
    raw = """
# Short-term memories

- [2026-01-31T00:00:00Z] (fact) timezone: UTC
- (preference) concise answers
- preference: dark theme
- just a bullet
not a bullet
""".strip()

    parsed = parse_short_term_memories(raw)

    assert "timezone: UTC" in parsed.facts
    assert "just a bullet" in parsed.facts
    assert "concise answers" in parsed.preferences
    assert "dark theme" in parsed.preferences


def test_append_and_read_roundtrip():
    tmp = tempfile.mkdtemp()
    try:
        vault_root = tmp
        user_id = "u1"

        p = append_memory(vault_root, user_id, kind="fact", text="timezone: UTC")
        assert p.exists() is True

        p2 = append_memory(vault_root, user_id, kind="preference", text="concise")
        assert p2 == p

        parsed = read_parsed(vault_root, user_id)
        assert parsed is not None
        assert any("timezone: UTC" in x for x in parsed.facts)
        assert any("concise" in x for x in parsed.preferences)

        # File is inside me/<user_id>/short_term_memories.md
        # macOS temp dirs may canonicalize to /private/var/...; compare resolved.
        expected = (Path(tmp) / "me" / user_id / "short_term_memories.md").resolve()
        assert expected == p.resolve()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
