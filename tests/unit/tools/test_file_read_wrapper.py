from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.tools import file_read_env as file_read_env_module


def test_file_read_wrapper_supports_tool_positional_arg(monkeypatch, tmp_path):
    """Some strands_tools versions require a positional `tool` arg; our wrapper should handle it."""

    def _fake_base_file_read(tool, *, path: str, mode: str = "view", **_kwargs):
        assert isinstance(tool, dict)
        assert tool.get("name") == "file_read"
        return {"content": f"{mode}:{path}"}

    monkeypatch.setattr(file_read_env_module, "_base_file_read", _fake_base_file_read)

    # Set up context vars so _allowed_roots() includes the workspace directory.
    workspace_dir = tmp_path / "workspace"
    user_work = workspace_dir / "test-user"
    user_work.mkdir(parents=True, exist_ok=True)

    cfg = SimpleNamespace(
        skills_base_dir=str(tmp_path / "skills"),
        shared_skills_dir=str(tmp_path / "shared"),
        working_folder_base_dir=str(workspace_dir),
    )
    file_read_env_module.set_file_read_context(user_id="test-user", config=cfg)

    # Create a test file inside the user's workspace directory.
    test_file = user_work / "test_x.txt"
    test_file.write_text("test content")

    try:
        out = file_read_env_module.file_read(path=str(test_file), mode="view")
        assert isinstance(out, dict)
        # Check that the path was resolved correctly
        assert "view:" in out.get("content", "")
        assert "test_x.txt" in out.get("content", "")
    finally:
        # Reset context vars
        file_read_env_module._current_user_id_var.set(None)
        file_read_env_module._config_var.set(None)
