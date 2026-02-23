import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.config import (
    refresh_runtime_env_from_secrets,
    resolve_user_skills_dir,
)
from app.tools.skill_secrets import set_cached_skill_secrets


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


@pytest.fixture(autouse=True)
def _clear_skill_secrets_cache():
    """Reset the in-memory skill secrets cache between tests."""
    set_cached_skill_secrets({})
    yield
    set_cached_skill_secrets({})


def test_example_template_renders_to_workspace_tmp_and_exports_config_env(tmp_path, monkeypatch):
    """Real filesystem test: *_example under per-user skill dir becomes a rendered file
    in workspace/<user>/tmp/, and exports <SKILL>_CONFIG for the canonical
    <skill>.toml_example naming.
    """

    # Isolate environment mutations to this test.
    monkeypatch.delenv("HIMALAYA_CONFIG", raising=False)

    cfg = SimpleNamespace(
        skills_base_dir=str(tmp_path / "skills"),
        shared_skills_dir=str(tmp_path / "skills" / "shared"),
        user_skills_dir_template=None,
        working_folder_base_dir=str(tmp_path / "workspace"),
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

    # Populate the in-memory DB cache (replaces per-user skills_secrets.yml).
    set_cached_skill_secrets({
        "himalaya": {
            "GMAIL": "iliagerman@gmail.com",
            "PASSWORD": "quujoeouvaomcfjd",
        },
    })

    # Global secrets file can be empty for this test.
    global_secrets = tmp_path / "secrets.yml"
    _write_yaml(global_secrets, {})

    refresh_runtime_env_from_secrets(
        secrets_path=global_secrets,
        user_id=user_id,
        skill_names=["himalaya"],
        config=cfg,
    )

    # Rendered file goes to workspace/<user>/tmp/.
    workspace_tmp = tmp_path / "workspace" / user_id / "tmp"
    expected_out = workspace_tmp / "himalaya.toml"
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
        working_folder_base_dir=str(tmp_path / "workspace"),
    )

    user_id = "u_test2"
    user_dir = resolve_user_skills_dir(cfg, user_id, create=True)

    skill_dir = user_dir / "foo"
    skill_dir.mkdir(parents=True, exist_ok=True)

    (skill_dir / "config.toml_example").write_text('token = "[TOKEN]"\n', encoding="utf-8")

    # Populate the in-memory DB cache.
    set_cached_skill_secrets({"foo": {"TOKEN": "abc123"}})

    global_secrets = tmp_path / "secrets.yml"
    _write_yaml(global_secrets, {})

    refresh_runtime_env_from_secrets(
        secrets_path=global_secrets,
        user_id=user_id,
        skill_names=["foo"],
        config=cfg,
    )

    # Because the template was config.toml_example, the output should be foo__config.toml
    workspace_tmp = tmp_path / "workspace" / user_id / "tmp"
    expected_out = workspace_tmp / "foo__config.toml"
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
        working_folder_base_dir=str(tmp_path / "workspace"),
    )

    user_id = "u_test3"
    user_dir = resolve_user_skills_dir(cfg, user_id, create=True)

    skill_dir = user_dir / "himalaya"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "himalaya.toml_example").write_text('email = "[GMAIL]"\n', encoding="utf-8")

    # Populate the in-memory DB cache.
    set_cached_skill_secrets({"himalaya": {"GMAIL": "a@b.com"}})

    global_secrets = tmp_path / "secrets.yml"
    _write_yaml(global_secrets, {})

    # Create an empty directory where the output file should be.
    workspace_tmp = tmp_path / "workspace" / user_id / "tmp"
    workspace_tmp.mkdir(parents=True, exist_ok=True)
    expected_out = workspace_tmp / "himalaya.toml"
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
