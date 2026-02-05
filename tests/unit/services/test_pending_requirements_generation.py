"""Unit tests for requirements generation from skill scripts."""

import shutil
import tempfile
from pathlib import Path

import pytest

from app.config import AgentConfig
from app.services.pending_skill_service import PendingSkillService


@pytest.fixture
def temp_skills_root():
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


def test_generate_requirements_creates_file_when_missing(temp_skills_root: Path):
    config = _config_for(temp_skills_root)
    svc = PendingSkillService(config)

    skill_dir = temp_skills_root / "user1" / "pending" / "reqtest"
    skill_dir.mkdir(parents=True)

    # requests should be inferred
    (skill_dir / "skill.py").write_text(
        "import requests\n\nprint(requests.__version__)\n",
        encoding="utf-8",
    )

    candidates = svc.list_pending(user_id="user1", include_shared=False)
    assert len(candidates) == 1

    report = svc.generate_requirements(candidates[0])
    assert report["ok"] is True
    assert report["generated"] is True

    req = skill_dir / "requirements.txt"
    assert req.exists()
    txt = req.read_text(encoding="utf-8")
    assert "requests" in txt


def test_generate_requirements_does_not_create_when_only_stdlib(temp_skills_root: Path):
    config = _config_for(temp_skills_root)
    svc = PendingSkillService(config)

    skill_dir = temp_skills_root / "user1" / "pending" / "stdlib"
    skill_dir.mkdir(parents=True)

    (skill_dir / "skill.py").write_text(
        "import os\nimport json\nfrom pathlib import Path\n",
        encoding="utf-8",
    )

    c = svc.list_pending(user_id="user1", include_shared=False)[0]
    report = svc.generate_requirements(c)
    assert report["ok"] is True
    assert report["generated"] is False
    assert not (skill_dir / "requirements.txt").exists()
