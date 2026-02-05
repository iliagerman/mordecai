"""Unit tests for pending-skill dependency inference + per-skill venv install.

We mock subprocess calls so the tests do not require uv/pip/network.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.config import AgentConfig
from app.services.pending_skill_service import PendingSkillService


@pytest.fixture
def temp_skills_root() -> Iterator[Path]:
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


def _config_for(root: Path) -> AgentConfig:
    return AgentConfig(
        telegram_bot_token="test-token",
        skills_base_dir=str(root),
        shared_skills_dir=str(root / "shared"),
        session_storage_dir=str(root / "sessions"),
        pending_skills_preflight_enabled=False,
        pending_skills_generate_requirements=True,
    )


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_onboard_installs_deps_into_skill_local_venv(monkeypatch, temp_skills_root: Path):
    config = _config_for(temp_skills_root)
    svc = PendingSkillService(config)

    pending_skill = temp_skills_root / "user1" / "pending" / "dep-skill"
    pending_skill.mkdir(parents=True)

    # Script import implies a dependency, so requirements.txt will be generated.
    (pending_skill / "skill.py").write_text("import requests\n", encoding="utf-8")

    def fake_run(cmd, cwd=None, env=None, capture_output=None, text=None, timeout=None):
        # uv venv <path>
        if cmd[:2] == ["uv", "venv"]:
            venv_dir = Path(cmd[2])
            (venv_dir / "bin").mkdir(parents=True, exist_ok=True)
            (venv_dir / "bin" / "python").write_text("#!/usr/bin/env python\n", encoding="utf-8")
            return _Proc(0)
        # uv pip install --python <py> -r requirements.txt
        if cmd[:3] == ["uv", "pip", "install"]:
            # best-effort: simulate that any console scripts would exist
            # (skill-specific tests create the exact bin when needed)
            return _Proc(0)
        # smoke test: <venv_python> <script>
        if str(cmd[0]).endswith("/.venv/bin/python") and str(cmd[1]).endswith("skill.py"):
            return _Proc(0)
        raise AssertionError(f"Unexpected subprocess command: {cmd}")

    monkeypatch.setattr("subprocess.run", fake_run)

    result = svc.onboard_pending(user_id="user1", scope="user")
    assert result["failed"] == 0
    assert result["onboarded"] == 1

    active = temp_skills_root / "user1" / "dep-skill"
    assert active.exists()
    assert (active / "requirements.txt").exists()
    assert (active / ".venv" / "bin" / "python").exists()


def test_onboard_writes_failed_json_when_dependency_install_fails(monkeypatch, temp_skills_root: Path):
    config = _config_for(temp_skills_root)
    svc = PendingSkillService(config)

    pending_skill = temp_skills_root / "user1" / "pending" / "dep-fail"
    pending_skill.mkdir(parents=True)
    (pending_skill / "skill.py").write_text("import requests\n", encoding="utf-8")

    def fake_run(cmd, cwd=None, env=None, capture_output=None, text=None, timeout=None):
        if cmd[:2] == ["uv", "venv"]:
            venv_dir = Path(cmd[2])
            (venv_dir / "bin").mkdir(parents=True, exist_ok=True)
            (venv_dir / "bin" / "python").write_text("#!/usr/bin/env python\n", encoding="utf-8")
            return _Proc(0)
        if cmd[:3] == ["uv", "pip", "install"]:
            return _Proc(1, stderr="boom")
        raise AssertionError(f"Unexpected subprocess command: {cmd}")

    monkeypatch.setattr("subprocess.run", fake_run)

    result = svc.onboard_pending(user_id="user1", scope="user")
    assert result["onboarded"] == 0
    assert result["failed"] == 1

    # Skill should remain pending and have FAILED.json
    assert pending_skill.exists()
    assert (pending_skill / "FAILED.json").exists()


def test_generate_requirements_merges_into_existing_file(temp_skills_root: Path):
    config = _config_for(temp_skills_root)
    svc = PendingSkillService(config)

    pending_skill = temp_skills_root / "user1" / "pending" / "merge"
    pending_skill.mkdir(parents=True)
    (pending_skill / "skill.py").write_text("import requests\n", encoding="utf-8")

    # existing requirements already has numpy
    (pending_skill / "requirements.txt").write_text("numpy\n", encoding="utf-8")

    c = svc.list_pending(user_id="user1", include_shared=False)[0]
    rep = svc.generate_requirements(c)

    assert rep["ok"] is True
    assert rep["generated"] is True
    assert rep["created"] is False
    assert "requests" in rep.get("added", [])

    txt = (pending_skill / "requirements.txt").read_text(encoding="utf-8")
    assert "numpy" in txt
    assert "requests" in txt
    assert "AUTO-GENERATED" in txt


def test_local_imports_are_not_added_to_requirements(temp_skills_root: Path):
    config = _config_for(temp_skills_root)
    svc = PendingSkillService(config)

    pending_skill = temp_skills_root / "user1" / "pending" / "local"
    pending_skill.mkdir(parents=True)
    (pending_skill / "foo.py").write_text("x = 1\n", encoding="utf-8")
    (pending_skill / "skill.py").write_text("import foo\nimport requests\n", encoding="utf-8")

    c = svc.list_pending(user_id="user1", include_shared=False)[0]
    rep = svc.generate_requirements(c)

    assert rep["ok"] is True
    req_path = pending_skill / "requirements.txt"
    assert req_path.exists()
    txt = req_path.read_text(encoding="utf-8")
    assert "requests" in txt
    assert "foo" not in txt


def test_onboard_writes_missing_modules_when_script_smoke_test_fails(monkeypatch, temp_skills_root: Path):
    config = _config_for(temp_skills_root)
    svc = PendingSkillService(config)

    pending_skill = temp_skills_root / "user1" / "pending" / "runtime-missing"
    pending_skill.mkdir(parents=True)

    # Dynamic import won't be detected by AST import scanning.
    (pending_skill / "skill.py").write_text(
        "__import__('totally_missing_pkg_123')\n",
        encoding="utf-8",
    )

    def fake_run(cmd, cwd=None, env=None, capture_output=None, text=None, timeout=None):
        if cmd[:2] == ["uv", "venv"]:
            venv_dir = Path(cmd[2])
            (venv_dir / "bin").mkdir(parents=True, exist_ok=True)
            (venv_dir / "bin" / "python").write_text("#!/usr/bin/env python\n", encoding="utf-8")
            return _Proc(0)
        if cmd[:3] == ["uv", "pip", "install"]:
            # No requirements.txt should be generated, but keep this permissive.
            return _Proc(0)
        # smoke test: <python> <script>
        if str(cmd[0]).endswith("/.venv/bin/python") and str(cmd[1]).endswith("skill.py"):
            return _Proc(
                1,
                stderr="ModuleNotFoundError: No module named 'totally_missing_pkg_123'\n",
            )
        raise AssertionError(f"Unexpected subprocess command: {cmd}")

    monkeypatch.setattr("subprocess.run", fake_run)

    result = svc.onboard_pending(user_id="user1", scope="user")
    assert result["onboarded"] == 0
    assert result["failed"] == 1

    failed_path = pending_skill / "FAILED.json"
    assert failed_path.exists()
    failed_json = failed_path.read_text(encoding="utf-8")
    assert "run_scripts_smoke_test" in failed_json
    assert "totally_missing_pkg_123" in failed_json


def test_skill_md_declared_pip_install_generates_requirements_and_bin(monkeypatch, temp_skills_root: Path):
    config = _config_for(temp_skills_root)
    svc = PendingSkillService(config)

    pending_skill = temp_skills_root / "shared" / "pending" / "nano-pdf"
    pending_skill.mkdir(parents=True)

    # No scripts; requirements should come from SKILL.md install directive
    (pending_skill / "SKILL.md").write_text(
        "---\n"
        "name: nano_pdf\n"
        "requires:\n"
        "  bins:\n"
        "    - nano-pdf\n"
        "install:\n"
        "  - kind: pip\n"
        "    package: nano-pdf\n"
        "---\n\n"
        "# nano-pdf\n",
        encoding="utf-8",
    )

    def fake_run(cmd, cwd=None, env=None, capture_output=None, text=None, timeout=None):
        if cmd[:2] == ["uv", "venv"]:
            venv_dir = Path(cmd[2])
            (venv_dir / "bin").mkdir(parents=True, exist_ok=True)
            (venv_dir / "bin" / "python").write_text("#!/usr/bin/env python\n", encoding="utf-8")
            return _Proc(0)
        if cmd[:3] == ["uv", "pip", "install"]:
            # simulate console script
            py = Path(cmd[cmd.index("--python") + 1])
            venv_bin = py.parent
            (venv_bin / "nano-pdf").write_text("#!/usr/bin/env sh\n", encoding="utf-8")
            return _Proc(0)
        raise AssertionError(f"Unexpected subprocess command: {cmd}")

    monkeypatch.setattr("subprocess.run", fake_run)

    # shared scope onboarding
    result = svc.onboard_pending(user_id="user1", scope="shared")
    assert result["failed"] == 0
    assert result["onboarded"] == 1

    active = temp_skills_root / "shared" / "nano-pdf"
    assert active.exists()
    req = (active / "requirements.txt").read_text(encoding="utf-8")
    assert "nano-pdf" in req
    assert (active / ".venv" / "bin" / "nano-pdf").exists()
