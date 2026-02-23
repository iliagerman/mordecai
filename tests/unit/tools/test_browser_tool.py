"""Unit tests for browser_tool.py (AgentCoreBrowser-based).

Tests:
- MordecaiBrowser.__init__ creates instance with correct region/user_id
- get_or_create_browser() factory caching behavior
- Cookie format conversion (DB <-> CDP round-trip)
- _extract_domain utility
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.domain import BrowserCookie


class TestExtractDomain:
    """Tests for the _extract_domain utility."""

    def test_extract_domain_from_full_url(self):
        from app.tools.browser_tool import _extract_domain

        assert _extract_domain("https://outlook.office.com/mail/inbox") == "outlook.office.com"

    def test_extract_domain_from_url_with_port(self):
        from app.tools.browser_tool import _extract_domain

        assert _extract_domain("https://example.com:8443/path") == "example.com:8443"

    def test_extract_domain_from_bare_url(self):
        from app.tools.browser_tool import _extract_domain

        assert _extract_domain("https://example.com") == "example.com"

    def test_extract_domain_fallback(self):
        from app.tools.browser_tool import _extract_domain

        # No scheme → urlparse can't extract netloc, falls back to the raw string
        result = _extract_domain("not-a-url")
        assert result == "not-a-url"


class TestLoadCookiesForDomain:
    """Tests for _load_cookies_for_domain."""

    @pytest.fixture
    def mock_cookie_dao(self):
        dao = AsyncMock()
        return dao

    @pytest.mark.asyncio
    async def test_loads_and_converts_cookies(self, mock_cookie_dao):
        from app.tools.browser_tool import _load_cookies_for_domain

        future_time = datetime.utcnow() + timedelta(hours=1)
        mock_cookie_dao.get_cookies.return_value = [
            BrowserCookie(
                user_id="u1",
                domain=".example.com",
                name="session",
                value="abc123",
                path="/",
                expires=future_time,
                http_only=True,
                secure=True,
                same_site="Lax",
            ),
        ]

        result = await _load_cookies_for_domain(mock_cookie_dao, "u1", ".example.com")

        assert len(result) == 1
        cookie = result[0]
        assert cookie["name"] == "session"
        assert cookie["value"] == "abc123"
        assert cookie["domain"] == ".example.com"
        assert cookie["path"] == "/"
        assert cookie["httpOnly"] is True
        assert cookie["secure"] is True
        assert cookie["sameSite"] == "Lax"
        assert "expires" in cookie

    @pytest.mark.asyncio
    async def test_skips_expired_cookies(self, mock_cookie_dao):
        from app.tools.browser_tool import _load_cookies_for_domain

        past_time = datetime.utcnow() - timedelta(hours=1)
        mock_cookie_dao.get_cookies.return_value = [
            BrowserCookie(
                user_id="u1",
                domain=".example.com",
                name="old",
                value="expired",
                path="/",
                expires=past_time,
            ),
        ]

        result = await _load_cookies_for_domain(mock_cookie_dao, "u1", ".example.com")
        assert result == []

    @pytest.mark.asyncio
    async def test_handles_dao_exception(self, mock_cookie_dao):
        from app.tools.browser_tool import _load_cookies_for_domain

        mock_cookie_dao.get_cookies.side_effect = RuntimeError("DB error")

        result = await _load_cookies_for_domain(mock_cookie_dao, "u1", ".example.com")
        assert result == []

    @pytest.mark.asyncio
    async def test_omits_optional_fields_when_false(self, mock_cookie_dao):
        from app.tools.browser_tool import _load_cookies_for_domain

        mock_cookie_dao.get_cookies.return_value = [
            BrowserCookie(
                user_id="u1",
                domain=".example.com",
                name="basic",
                value="val",
                path="/",
                expires=None,
                http_only=False,
                secure=False,
                same_site=None,
            ),
        ]

        result = await _load_cookies_for_domain(mock_cookie_dao, "u1", ".example.com")
        assert len(result) == 1
        cookie = result[0]
        assert "httpOnly" not in cookie
        assert "secure" not in cookie
        assert "sameSite" not in cookie
        assert "expires" not in cookie


class TestSaveCookiesFromCdp:
    """Tests for _save_cookies_from_cdp."""

    @pytest.fixture
    def mock_cookie_dao(self):
        dao = AsyncMock()
        dao.upsert_cookies.return_value = 2
        return dao

    @pytest.mark.asyncio
    async def test_converts_and_saves_cookies(self, mock_cookie_dao):
        from app.tools.browser_tool import _save_cookies_from_cdp

        cdp_cookies = [
            {
                "name": "session",
                "value": "abc",
                "domain": ".example.com",
                "path": "/",
                "expires": (datetime.utcnow() + timedelta(hours=1)).timestamp(),
                "httpOnly": True,
                "secure": True,
                "sameSite": "Strict",
            },
            {
                "name": "pref",
                "value": "dark",
                "domain": ".example.com",
                "path": "/settings",
            },
        ]

        result = await _save_cookies_from_cdp(mock_cookie_dao, "u1", cdp_cookies)
        assert result == 2
        mock_cookie_dao.upsert_cookies.assert_called_once()

        args = mock_cookie_dao.upsert_cookies.call_args
        assert args[0][0] == "u1"
        saved_cookies = args[0][1]
        assert len(saved_cookies) == 2
        assert saved_cookies[0].name == "session"
        assert saved_cookies[0].http_only is True
        assert saved_cookies[1].name == "pref"
        assert saved_cookies[1].http_only is False

    @pytest.mark.asyncio
    async def test_empty_cookies_returns_zero(self, mock_cookie_dao):
        from app.tools.browser_tool import _save_cookies_from_cdp

        result = await _save_cookies_from_cdp(mock_cookie_dao, "u1", [])
        assert result == 0
        mock_cookie_dao.upsert_cookies.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_dao_exception(self, mock_cookie_dao):
        from app.tools.browser_tool import _save_cookies_from_cdp

        mock_cookie_dao.upsert_cookies.side_effect = RuntimeError("DB error")

        result = await _save_cookies_from_cdp(
            mock_cookie_dao, "u1", [{"name": "x", "value": "y", "domain": ".test.com"}]
        )
        assert result == 0

    @pytest.mark.asyncio
    async def test_handles_invalid_expires(self, mock_cookie_dao):
        from app.tools.browser_tool import _save_cookies_from_cdp

        cdp_cookies = [
            {
                "name": "bad",
                "value": "v",
                "domain": ".example.com",
                "expires": -1,
            },
        ]

        result = await _save_cookies_from_cdp(mock_cookie_dao, "u1", cdp_cookies)
        assert result == 2  # uses the mock's return value
        saved = mock_cookie_dao.upsert_cookies.call_args[0][1]
        assert saved[0].expires is None


class TestCookieRoundTrip:
    """Test that cookies survive a DB -> CDP -> DB round-trip."""

    @pytest.mark.asyncio
    async def test_round_trip(self):
        from app.tools.browser_tool import _load_cookies_for_domain, _save_cookies_from_cdp

        future = datetime.utcnow() + timedelta(days=1)
        original = BrowserCookie(
            user_id="u1",
            domain=".example.com",
            name="token",
            value="secret",
            path="/api",
            expires=future,
            http_only=True,
            secure=True,
            same_site="None",
        )

        # DB -> CDP format
        mock_load_dao = AsyncMock()
        mock_load_dao.get_cookies.return_value = [original]
        cdp_cookies = await _load_cookies_for_domain(mock_load_dao, "u1", ".example.com")
        assert len(cdp_cookies) == 1

        # CDP -> DB format
        mock_save_dao = AsyncMock()
        mock_save_dao.upsert_cookies.return_value = 1
        await _save_cookies_from_cdp(mock_save_dao, "u1", cdp_cookies)

        saved = mock_save_dao.upsert_cookies.call_args[0][1]
        assert len(saved) == 1
        roundtripped = saved[0]
        assert roundtripped.domain == original.domain
        assert roundtripped.name == original.name
        assert roundtripped.value == original.value
        assert roundtripped.path == original.path
        assert roundtripped.http_only == original.http_only
        assert roundtripped.secure == original.secure
        assert roundtripped.same_site == original.same_site


class TestMordecaiBrowser:
    """Tests for the MordecaiBrowser class."""

    def test_init_creates_delegate_with_region(self):
        mock_acb_cls = MagicMock()
        mock_module = MagicMock(AgentCoreBrowser=mock_acb_cls)

        with patch.dict("sys.modules", {"strands_tools.browser": mock_module}):
            mock_dao = MagicMock()
            from app.tools.browser_tool import MordecaiBrowser

            instance = MordecaiBrowser(region="eu-west-1", user_id="u1", cookie_dao=mock_dao)

            mock_acb_cls.assert_called_once_with(region="eu-west-1")
            assert instance._user_id == "u1"
            assert instance._cookie_dao is mock_dao

    def test_browser_property_returns_delegate_browser(self):
        mock_delegate = MagicMock()
        mock_acb_cls = MagicMock(return_value=mock_delegate)
        mock_module = MagicMock(AgentCoreBrowser=mock_acb_cls)

        with patch.dict("sys.modules", {"strands_tools.browser": mock_module}):
            mock_dao = MagicMock()
            from app.tools.browser_tool import MordecaiBrowser

            instance = MordecaiBrowser(region="us-west-2", user_id="u1", cookie_dao=mock_dao)
            assert instance.browser is mock_delegate.browser


class TestNotifyUserLiveView:
    """Tests for the _notify_user_live_view helper."""

    def test_queues_progress_message_when_callback_set(self):
        from app.tools.send_progress import _progress_callback, get_pending_progress_messages

        # Set a dummy callback so the queue mechanism activates
        dummy_cb = AsyncMock()
        token = _progress_callback.set(dummy_cb)
        try:
            from app.tools.browser_tool import _notify_user_live_view

            _notify_user_live_view("https://example.com/live-view")

            messages = get_pending_progress_messages()
            assert len(messages) == 1
            assert "https://example.com/live-view" in messages[0]
            assert "Browser session started" in messages[0]
        finally:
            _progress_callback.reset(token)

    def test_silent_noop_when_no_callback(self):
        """Should not raise even when no progress callback is set."""
        from app.tools.browser_tool import _notify_user_live_view

        # No callback set — should be a silent no-op
        _notify_user_live_view("https://example.com/live-view")


class TestGetOrCreateBrowser:
    """Tests for the get_or_create_browser factory."""

    def _make_config(self, region="us-west-2"):
        config = MagicMock()
        config.browser_region = region
        return config

    @patch("app.tools.browser_tool.MordecaiBrowser")
    def test_creates_new_instance(self, mock_browser_cls):
        import app.tools.browser_tool as bt

        # Clear cache
        bt._browser_cache.clear()

        config = self._make_config("ap-southeast-1")
        mock_dao = MagicMock()
        mock_instance = MagicMock()
        mock_browser_cls.return_value = mock_instance

        result = bt.get_or_create_browser("user1", config, mock_dao)

        mock_browser_cls.assert_called_once_with(
            region="ap-southeast-1",
            user_id="user1",
            cookie_dao=mock_dao,
        )
        assert result is mock_instance
        bt._browser_cache.clear()

    @patch("app.tools.browser_tool.MordecaiBrowser")
    def test_caches_instance(self, mock_browser_cls):
        import app.tools.browser_tool as bt

        bt._browser_cache.clear()

        config = self._make_config()
        mock_dao = MagicMock()
        mock_instance = MagicMock()
        mock_browser_cls.return_value = mock_instance

        first = bt.get_or_create_browser("user2", config, mock_dao)
        second = bt.get_or_create_browser("user2", config, mock_dao)

        assert first is second
        assert mock_browser_cls.call_count == 1
        bt._browser_cache.clear()

    @patch("app.tools.browser_tool.MordecaiBrowser")
    def test_different_users_get_different_instances(self, mock_browser_cls):
        import app.tools.browser_tool as bt

        bt._browser_cache.clear()

        config = self._make_config()
        mock_dao = MagicMock()

        mock_browser_cls.side_effect = [MagicMock(), MagicMock()]

        first = bt.get_or_create_browser("alice", config, mock_dao)
        second = bt.get_or_create_browser("bob", config, mock_dao)

        assert first is not second
        assert mock_browser_cls.call_count == 2
        bt._browser_cache.clear()

    @patch("app.tools.browser_tool.MordecaiBrowser")
    def test_uses_default_region_when_missing(self, mock_browser_cls):
        import app.tools.browser_tool as bt

        bt._browser_cache.clear()

        # Config without browser_region attribute
        config = MagicMock(spec=[])
        mock_dao = MagicMock()

        bt.get_or_create_browser("user3", config, mock_dao)

        call_kwargs = mock_browser_cls.call_args[1]
        assert call_kwargs["region"] == "us-west-2"
        bt._browser_cache.clear()
