"""Unit tests for short-term memory vault helpers."""

import shutil
import tempfile
from pathlib import Path

from app.tools.short_term_memory_vault import (
    append_memory,
    list_user_ids,
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
        scratchpad_dir = str(Path(tmp) / "scratchpad")

        p = append_memory(scratchpad_dir, kind="fact", text="timezone: UTC")
        assert p.exists() is True

        p2 = append_memory(scratchpad_dir, kind="preference", text="concise")
        assert p2 == p

        parsed = read_parsed(scratchpad_dir)
        assert parsed is not None
        assert any("timezone: UTC" in x for x in parsed.facts)
        assert any("concise" in x for x in parsed.preferences)

        # File is inside scratchpad/stm.md
        # macOS temp dirs may canonicalize to /private/var/...; compare resolved.
        expected = (Path(tmp) / "scratchpad" / "stm.md").resolve()
        assert expected == p.resolve()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_list_user_ids_scans_workspace():
    """list_user_ids returns user ids whose workspace has a scratchpad/ dir."""
    tmp = tempfile.mkdtemp()
    try:
        workspace = Path(tmp) / "workspace"
        workspace.mkdir()

        # user with scratchpad -> included
        (workspace / "alice" / "scratchpad").mkdir(parents=True)
        # user without scratchpad -> excluded
        (workspace / "bob").mkdir(parents=True)
        # reserved name -> excluded
        (workspace / "default" / "scratchpad").mkdir(parents=True)
        # hidden dir -> excluded
        (workspace / ".hidden" / "scratchpad").mkdir(parents=True)
        # another valid user
        (workspace / "carol" / "scratchpad").mkdir(parents=True)

        ids = list_user_ids(str(workspace))
        assert ids == ["alice", "carol"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
