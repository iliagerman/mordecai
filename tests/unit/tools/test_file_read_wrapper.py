from __future__ import annotations

from app.tools import file_read_env as file_read_env_module


def test_file_read_wrapper_supports_tool_positional_arg(monkeypatch):
    """Some strands_tools versions require a positional `tool` arg; our wrapper should handle it."""

    def _fake_base_file_read(tool, *, path: str, mode: str = "view", **_kwargs):
        assert isinstance(tool, dict)
        assert tool.get("name") == "file_read"
        return {"content": f"{mode}:{path}"}

    monkeypatch.setattr(file_read_env_module, "_base_file_read", _fake_base_file_read)

    out = file_read_env_module.file_read(path="/tmp/x.txt", mode="view")
    assert isinstance(out, dict)
    assert out.get("content") == "view:/tmp/x.txt"
