import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.config import AgentConfig, resolve_user_skills_dir
from app.enums import ModelProvider
from app.services.agent_service import AgentService


@pytest.fixture
def temp_dir():
    temp_path = tempfile.mkdtemp()
    yield temp_path
    # Cleanup is best-effort; tests use temp paths.


def test_missing_skill_requirements_respects_conditional_requires_config(temp_dir):
    config = AgentConfig(
        model_provider=ModelProvider.BEDROCK,
        bedrock_model_id="anthropic.claude-3-sonnet-20240229-v1:0",
        telegram_bot_token="test-token",
        session_storage_dir=temp_dir,
        skills_base_dir=temp_dir,
    )

    service = AgentService(config)
    user_id = "alice"

    user_dir = resolve_user_skills_dir(config, user_id, create=True)
    skill_dir = user_dir / "himalaya"
    skill_dir.mkdir(parents=True, exist_ok=True)

    # Provider-specific config requirements should not be considered missing
    # unless EMAIL_PROVIDER selects that provider.
    (skill_dir / "SKILL.md").write_text(
        """---
name: himalaya
description: test
requires:
  config:
    - name: EMAIL_PROVIDER
    - name: GMAIL
      when:
        config: EMAIL_PROVIDER
        equals: gmail
    - name: PASSWORD
      when:
        config: EMAIL_PROVIDER
        equals: gmail
    - name: OUTLOOK_EMAIL
      when:
        config: EMAIL_PROVIDER
        equals: outlook
---
\n# Himalaya\n\n```bash\necho ok\n```\n""",
        encoding="utf-8",
    )

    merged_secrets = {
        "skills": {
            "himalaya": {
                "EMAIL_PROVIDER": "gmail",
                "GMAIL": "alice@example.com",
                "PASSWORD": "dummy",
                # No OUTLOOK_EMAIL on purpose.
            }
        }
    }

    with (
        patch("app.services.agent.skills.SkillRepository.load_merged_skill_secrets", return_value=merged_secrets),
        patch("app.config.refresh_runtime_env_from_secrets"),
    ):
        missing = service._get_missing_skill_requirements(user_id)

    assert "himalaya" not in missing
