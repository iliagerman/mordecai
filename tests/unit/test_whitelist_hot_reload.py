import os
from pathlib import Path

import yaml

from app.security.whitelist import is_whitelisted, live_allowed_users


def _write_secrets(path: Path, allowed_users: list[str] | None) -> None:
    data = {}
    if allowed_users is not None:
        data["allowed_users"] = allowed_users

    path.write_text(
        yaml.safe_dump(data, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )

    # Ensure mtime changes even on coarse filesystems.
    os.utime(path, None)


def test_live_allowed_users_reload_on_change(tmp_path: Path) -> None:
    secrets_path = tmp_path / "secrets.yml"

    _write_secrets(secrets_path, ["alice"])

    allowed = live_allowed_users(secrets_path)
    assert is_whitelisted("alice", allowed)
    assert not is_whitelisted("bob", allowed)

    _write_secrets(secrets_path, ["bob"])

    assert not is_whitelisted("alice", allowed)
    assert is_whitelisted("bob", allowed)


def test_live_allowed_users_missing_file_allows_all(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.yml"
    allowed = live_allowed_users(missing)

    # Empty whitelist => allow all (dev/test-friendly default)
    assert is_whitelisted("anyone", allowed)
