from __future__ import annotations

import os
from pathlib import Path

import yaml

from app.config import refresh_runtime_env_from_secrets


def test_refresh_runtime_env_unsets_stale_keys_on_user_switch(tmp_path: Path, monkeypatch):
    """Ensure skill env vars don't leak across users in a long-running process."""

    secrets_path = tmp_path / "secrets.yml"
    secrets_path.write_text(
        yaml.safe_dump(
            {
                "skills": {
                    "demo-skill": {
                        "users": {
                            "user1": {"env": {"DEMO_TOKEN": "u1-secret"}},
                            # user2 has no override and no global env
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("DEMO_TOKEN", raising=False)

    refresh_runtime_env_from_secrets(secrets_path=Path(secrets_path), user_id="user1")
    assert os.environ.get("DEMO_TOKEN") == "u1-secret"

    refresh_runtime_env_from_secrets(secrets_path=Path(secrets_path), user_id="user2")
    assert os.environ.get("DEMO_TOKEN") is None
