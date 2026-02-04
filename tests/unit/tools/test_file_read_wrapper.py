from __future__ import annotations

import tempfile
from pathlib import Path

from app.tools import file_read_env as file_read_env_module


def test_file_read_wrapper_supports_tool_positional_arg(monkeypatch):
    """Some strands_tools versions require a positional `tool` arg; our wrapper should handle it."""

    def _fake_base_file_read(tool, *, path: str, mode: str = "view", **_kwargs):
        assert isinstance(tool, dict)
        assert tool.get("name") == "file_read"
        return {"content": f"{mode}:{path}"}

    monkeypatch.setattr(file_read_env_module, "_base_file_read", _fake_base_file_read)

    # Use a path within the scratchpad directory (file_read only allows scratchpad/**)
    # Create a test file in scratchpad
    from app.config import SCRATCHPAD_DIRNAME
    repo_root = Path(__file__).resolve().parent.parent.parent
    scratchpad = repo_root / SCRATCHPAD_DIRNAME
    scratchpad.mkdir(parents=True, exist_ok=True)
    test_file = scratchpad / "test_x.txt"
    test_file.write_text("test content")

    try:
        out = file_read_env_module.file_read(path="scratchpad/test_x.txt", mode="view")
        assert isinstance(out, dict)
        # Check that the path was resolved correctly
        assert "view:" in out.get("content", "")
        assert "test_x.txt" in out.get("content", "")
    finally:
        # Cleanup
        if test_file.exists():
            test_file.unlink()
