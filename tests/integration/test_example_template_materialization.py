import os
from pathlib import Path
from types import SimpleNamespace

import yaml

from app.config import (
    refresh_runtime_env_from_secrets,
    resolve_user_skills_dir,
    resolve_user_skills_secrets_path,
)


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_example_template_renders_to_user_root_and_exports_config_env(tmp_path, monkeypatch):
    """Real filesystem test: *_example under per-user skill dir becomes a rendered file
    in the per-user skills root (same folder as skills_secrets.yml), and exports
    <SKILL>_CONFIG for the canonical <skill>.toml_example naming.
    """

    # Isolate environment mutations to this test.
    monkeypatch.delenv("HIMALAYA_CONFIG", raising=False)

    # Minimal config object for resolve_user_skills_dir/resolve_user_skills_secrets_path.
    cfg = SimpleNamespace(
        skills_base_dir=str(tmp_path / "skills"),
        shared_skills_dir=str(tmp_path / "skills" / "shared"),
        user_skills_dir_template=None,
    )

    user_id = "u_test"

    user_dir = resolve_user_skills_dir(cfg, user_id, create=True)
    skill_dir = user_dir / "himalaya"
    skill_dir.mkdir(parents=True, exist_ok=True)

    # Create a minimal example template.
    (skill_dir / "himalaya.toml_example").write_text(
        'email = "[GMAIL]"\npassword = "[PASSWORD]"\n',
        encoding="utf-8",
    )

    # Persist per-user secrets (the real source of placeholders).
    secrets_path = resolve_user_skills_secrets_path(cfg, user_id, create=True)
    _write_yaml(
        secrets_path,
        {
            "skills": {
                "himalaya": {
                    "GMAIL": "iliagerman@gmail.com",
                    "PASSWORD": "quujoeouvaomcfjd",
                }
            }
        },
    )

    # Global secrets file can be empty for this test.
    global_secrets = tmp_path / "secrets.yml"
    _write_yaml(global_secrets, {})

    refresh_runtime_env_from_secrets(
        secrets_path=global_secrets,
        user_id=user_id,
        skill_names=["himalaya"],
        config=cfg,
    )

    expected_out = user_dir / "himalaya.toml"
    assert expected_out.exists() and expected_out.is_file()

    rendered = expected_out.read_text(encoding="utf-8")
    assert "[GMAIL]" not in rendered
    assert "[PASSWORD]" not in rendered
    assert "iliagerman@gmail.com" in rendered
    assert "quujoeouvaomcfjd" in rendered

    assert os.environ.get("HIMALAYA_CONFIG") == str(expected_out)


def test_example_template_noncanonical_name_is_prefixed_to_avoid_collisions(tmp_path, monkeypatch):
    """If a template name doesn't already include the skill name, we prefix it so
    multiple skills can ship config.toml_example without collisions.
    """

    cfg = SimpleNamespace(
        skills_base_dir=str(tmp_path / "skills"),
        shared_skills_dir=str(tmp_path / "skills" / "shared"),
        user_skills_dir_template=None,
    )

    user_id = "u_test2"
    user_dir = resolve_user_skills_dir(cfg, user_id, create=True)

    skill_dir = user_dir / "foo"
    skill_dir.mkdir(parents=True, exist_ok=True)

    (skill_dir / "config.toml_example").write_text('token = "[TOKEN]"\n', encoding="utf-8")

    secrets_path = resolve_user_skills_secrets_path(cfg, user_id, create=True)
    _write_yaml(secrets_path, {"skills": {"foo": {"TOKEN": "abc123"}}})

    global_secrets = tmp_path / "secrets.yml"
    _write_yaml(global_secrets, {})

    refresh_runtime_env_from_secrets(
        secrets_path=global_secrets,
        user_id=user_id,
        skill_names=["foo"],
        config=cfg,
    )

    # Because the template was config.toml_example, the output should be foo__config.toml
    expected_out = user_dir / "foo__config.toml"
    assert expected_out.exists() and expected_out.is_file()
    assert expected_out.read_text(encoding="utf-8").strip() == 'token = "abc123"'


def test_example_template_recovers_if_destination_is_directory(tmp_path, monkeypatch):
    """If the rendered output path exists as an empty directory, replace it with a file.

    This guards against accidental `mkdir -p himalaya.toml` style mistakes.
    """

    cfg = SimpleNamespace(
        skills_base_dir=str(tmp_path / "skills"),
        shared_skills_dir=str(tmp_path / "skills" / "shared"),
        user_skills_dir_template=None,
    )

    user_id = "u_test3"
    user_dir = resolve_user_skills_dir(cfg, user_id, create=True)

    skill_dir = user_dir / "himalaya"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "himalaya.toml_example").write_text('email = "[GMAIL]"\n', encoding="utf-8")

    secrets_path = resolve_user_skills_secrets_path(cfg, user_id, create=True)
    _write_yaml(secrets_path, {"skills": {"himalaya": {"GMAIL": "a@b.com"}}})

    global_secrets = tmp_path / "secrets.yml"
    _write_yaml(global_secrets, {})

    # Create an empty directory where the output file should be.
    expected_out = user_dir / "himalaya.toml"
    expected_out.mkdir(parents=True, exist_ok=True)
    assert expected_out.exists() and expected_out.is_dir()

    refresh_runtime_env_from_secrets(
        secrets_path=global_secrets,
        user_id=user_id,
        skill_names=["himalaya"],
        config=cfg,
    )

    assert expected_out.exists() and expected_out.is_file()
    assert expected_out.read_text(encoding="utf-8").strip() == 'email = "a@b.com"'
