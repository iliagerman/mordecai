from pathlib import Path

import pytest

from app.tools.conversation_summary_vault import (
    conversation_summary_path,
    write_session_summary,
)


def test_conversation_summary_path_is_under_vault_root(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()

    p = conversation_summary_path(str(vault), user_id="u1", session_id="s1")
    assert p.is_absolute()
    assert str(p).startswith(str(vault.resolve()))
    assert p.name == "s1.md"


def test_conversation_summary_path_sanitizes_session_id(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()

    p = conversation_summary_path(str(vault), user_id="u1", session_id="../evil")
    # Should not traverse directories; should be a safe filename.
    assert p.name.endswith(".md")
    assert ".." not in p.name
    assert p.parent.name == "conversations"


def test_write_session_summary_writes_markdown(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()

    note_path = write_session_summary(
        str(vault),
        user_id="u1",
        session_id="session_123",
        summary="- A\n- B",
    )

    assert note_path.exists()
    content = note_path.read_text(encoding="utf-8")
    assert content.startswith("# Conversation summary")
    assert "session_id: session_123" in content
    assert "- A" in content


def test_write_session_summary_rejects_empty_summary(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()

    with pytest.raises(ValueError):
        write_session_summary(
            str(vault),
            user_id="u1",
            session_id="s1",
            summary="   ",
        )
