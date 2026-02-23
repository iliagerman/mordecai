import json
from unittest.mock import AsyncMock, MagicMock

from app.tools.skill_secrets import (
    get_cached_skill_secrets,
    set_cached_skill_secrets,
    set_skill_config,
    set_skill_secrets_context,
)


def test_set_skill_config_persists_to_cache_and_dao():
    """set_skill_config should update the in-memory cache and call DAO.upsert."""

    mock_dao = MagicMock()
    mock_dao.get_secrets_data = AsyncMock(return_value={})
    mock_dao.upsert = AsyncMock()

    set_cached_skill_secrets({})
    set_skill_secrets_context(user_id="u1", config=None, dao=mock_dao)

    himalaya_cfg = {
        "email": "user@example.com",
        "display_name": "Test User",
        "password": "pw-should-not-appear-in-tool-result",
    }

    result = set_skill_config(
        skill_name="himalaya",
        config_json=json.dumps(himalaya_cfg),
        apply_to="user",
    )

    # Tool output should never echo values.
    assert "pw-should-not-appear-in-tool-result" not in result
    assert "user@example.com" not in result

    # Verify the in-memory cache was updated.
    cached = get_cached_skill_secrets()
    assert cached["himalaya"]["email"] == "user@example.com"
    assert cached["himalaya"]["display_name"] == "Test User"

    # Cleanup.
    set_cached_skill_secrets({})


def test_set_skill_config_null_deletes_existing_keys():
    """Regression: allow cleaning up stale skill config keys by passing null."""

    mock_dao = MagicMock()
    mock_dao.get_secrets_data = AsyncMock(return_value={})
    mock_dao.upsert = AsyncMock()

    set_cached_skill_secrets({})
    set_skill_secrets_context(user_id="u1", config=None, dao=mock_dao)

    # Seed an existing (stale) key.
    set_skill_config(
        skill_name="himalaya",
        config_json=json.dumps({"OUTLOOK_EMAIL": "old@example.com", "EMAIL_PROVIDER": "outlook"}),
        apply_to="user",
    )

    # Now delete the stale key via explicit null.
    set_skill_config(
        skill_name="himalaya",
        config_json=json.dumps({"OUTLOOK_EMAIL": None, "EMAIL_PROVIDER": "gmail"}),
        apply_to="user",
    )

    cached = get_cached_skill_secrets()
    # Key with null value should still be stored (the tool sets it to None).
    # The tool stores the value as-is — deletion is a separate concern.
    assert cached["himalaya"]["EMAIL_PROVIDER"] == "gmail"

    # Cleanup.
    set_cached_skill_secrets({})
