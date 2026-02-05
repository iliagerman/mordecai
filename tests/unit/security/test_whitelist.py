"""Unit tests for whitelist normalization and enforcement."""

from app.security.whitelist import is_whitelisted, normalize_allowed_users


def test_normalize_allowed_users_strips_at_prefix():
    allowed = normalize_allowed_users(["@TestUser", "  @another  "])
    assert "testuser" in allowed
    assert "another" in allowed


def test_is_whitelisted_accepts_username_with_at_prefix():
    # allowed_users may be configured with an @prefix
    allowed_users = ["@testuser"]
    assert is_whitelisted("testuser", allowed_users) is True
    assert is_whitelisted("@testuser", allowed_users) is True


def test_is_whitelisted_empty_allowlist_allows_all():
    assert is_whitelisted("anyone", []) is True
    assert is_whitelisted("anyone", None) is True
