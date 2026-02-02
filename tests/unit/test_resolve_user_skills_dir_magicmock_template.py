from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from app.config import AgentConfig, resolve_user_skills_dir


def test_resolve_user_skills_dir_ignores_magicmock_template(tmp_path: Path) -> None:
    """Regression: don't create '<MagicMock ...>/' folders under repo root.

    When config is a MagicMock(spec=AgentConfig) and user_skills_dir_template is
    not explicitly set, attribute access returns another MagicMock.

    resolve_user_skills_dir must treat that as "unset" and fall back to
    skills_base_dir/<user_id>.
    """

    cfg = MagicMock(spec=AgentConfig, name="cfg")
    cfg.skills_base_dir = str(tmp_path / "skills")

    user_dir = resolve_user_skills_dir(cfg, "u1", create=True)

    assert user_dir == tmp_path / "skills" / "u1"
    assert user_dir.exists()
