from __future__ import annotations

import time

import pytest

from app.config import AgentConfig
from app.tools import shell_env as shell_env_module


@pytest.mark.integration
def test_shell_tool_executes_real_command_without_hanging(tmp_path, monkeypatch):
    """Regression: shell tool must not hang in headless/CI environments.

    This test exercises the *real* upstream `strands_tools.shell` implementation
    through our wrapper.

    Key behaviors we rely on:
    - The wrapper forces non-interactive mode when stdin is not a TTY.
    - A short timeout is passed through.
    - A trivial command completes and returns output in the expected tool shape.
    """

    monkeypatch.setenv("AGENT_TELEGRAM_BOT_TOKEN", "test-token")
    # Avoid any interactive confirmations even if upstream mis-detects interactivity.
    monkeypatch.setenv("BYPASS_TOOL_CONSENT", "true")
    monkeypatch.setenv("STRANDS_NON_INTERACTIVE", "true")

    secrets_path = tmp_path / "secrets.yml"
    secrets_path.write_text("skills: {}\n", encoding="utf-8")

    cfg = AgentConfig(telegram_bot_token="test-token", skills_base_dir=str(tmp_path / "skills"), working_folder_base_dir=str(tmp_path / "workspace"))
    shell_env_module.set_shell_env_context(
        user_id="u_real_shell", secrets_path=secrets_path, config=cfg
    )

    t0 = time.perf_counter()
    result = shell_env_module.shell(
        command='echo "SHELL_WRAPPER_OK"',
        work_dir=str(tmp_path),
        timeout_seconds=5,
    )
    dt = time.perf_counter() - t0

    assert dt < 10, "Shell tool should return promptly for a trivial command"
    assert isinstance(result, dict)

    # Upstream `strands_tools.shell` returns {status, content:[{text:...}, ...]}.
    content = result.get("content")
    assert isinstance(content, list)
    joined = "\n".join(str(item.get("text", "")) for item in content if isinstance(item, dict))

    assert "SHELL_WRAPPER_OK" in joined
