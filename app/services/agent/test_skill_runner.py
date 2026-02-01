from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.agent.skills import SkillRepository
from app.services.agent.types import SkillInfo

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DeterministicEchoSkillRunner:
    """Pytest-only deterministic execution for trivial echo-only skills."""

    config: Any
    skill_repo: SkillRepository
    get_working_dir: Callable[[str], Path]

    def maybe_run(self, *, user_id: str, message: str) -> str | None:
        msg_lower = (message or "").lower()
        if not msg_lower:
            return None

        # Only trigger when explicitly asking to use a skill.
        if not any(p in msg_lower for p in ("use ", "please use ")):
            return None

        skills: list[SkillInfo] = self.skill_repo.discover(user_id)
        if not skills:
            return None

        matches: list[SkillInfo] = []
        for s in skills:
            name = str(s.get("name") or "").strip()
            if name and name.lower() in msg_lower:
                matches.append(s)

        if len(matches) != 1:
            return None

        skill_path = str(matches[0].get("path") or "").strip()
        if not skill_path:
            return None

        skill_md = Path(skill_path) / "SKILL.md"
        if not skill_md.exists():
            return None

        try:
            content = skill_md.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return None

        cmd = self._extract_single_echo_command_from_skill_md(content)
        if not cmd:
            return None

        # Ensure shell wrapper context is set for this user.
        from app.tools import shell_env as shell_env_module

        shell_env_module.set_shell_env_context(
            user_id=user_id,
            secrets_path=getattr(self.config, "secrets_path", "secrets.yml"),
            config=self.config,
        )

        work_dir = str(self.get_working_dir(user_id))
        try:
            result = shell_env_module.shell(command=cmd, work_dir=work_dir)
        except Exception as e:
            logger.warning("Deterministic skill shell execution failed: %s", e)
            return None

        stdout = ""
        if isinstance(result, dict):
            stdout = str(result.get("stdout") or "")
        elif isinstance(result, str):
            stdout = result

        marker = self._extract_echo_marker(cmd)
        if marker and marker in stdout:
            return f"✅ **Skill executed successfully!**\n\n**Output:** `{marker}`\n"

        if stdout.strip():
            return f"✅ **Skill executed successfully!**\n\n**Output:**\n{stdout.strip()}\n"

        return None

    def _extract_single_echo_command_from_skill_md(self, content: str) -> str | None:
        if not content:
            return None

        m = re.search(r"```bash\s*(.*?)\s*```", content, flags=re.DOTALL | re.IGNORECASE)
        if not m:
            return None

        block = m.group(1)
        if not block:
            return None

        lines = [ln.strip() for ln in block.splitlines()]
        lines = [ln for ln in lines if ln and not ln.startswith("#")]
        if len(lines) != 1:
            return None

        cmd = lines[0]
        m_cmd = re.fullmatch(r"echo\s+(['\"])(.*?)\1", cmd)
        if not m_cmd:
            return None

        marker = m_cmd.group(2)
        if any(ch in marker for ch in ("`", "$", "\\")):
            return None

        return cmd

    def _extract_echo_marker(self, cmd: str) -> str | None:
        m = re.fullmatch(r"echo\s+(['\"])(.*)\1", cmd.strip())
        if not m:
            return None
        return m.group(2)
