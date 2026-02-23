"""Browser automation tool using Strands AgentCoreBrowser.

Provides a `MordecaiBrowser` that wraps AgentCoreBrowser with
cookie persistence hooks. The factory function `get_or_create_browser()`
caches instances per user_id.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from app.config import AgentConfig
    from app.dao.browser_cookie_dao import BrowserCookieDAO

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cookie format helpers (DB <-> Playwright/CDP)
# ---------------------------------------------------------------------------


def _extract_domain(url: str) -> str:
    """Extract the root domain from a URL."""
    parsed = urlparse(url)
    return parsed.netloc or parsed.hostname or url


async def _load_cookies_for_domain(
    cookie_dao: BrowserCookieDAO,
    user_id: str,
    domain: str,
) -> list[dict[str, Any]]:
    """Load stored cookies for a domain from the database.

    Returns cookies in CDP-compatible format.
    """
    try:
        cookies = await cookie_dao.get_cookies(user_id, domain=domain)
        cdp_cookies: list[dict[str, Any]] = []
        for c in cookies:
            if c.expires and c.expires < datetime.utcnow():
                continue
            cookie_dict: dict[str, Any] = {
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path,
            }
            if c.expires:
                cookie_dict["expires"] = c.expires.timestamp()
            if c.http_only:
                cookie_dict["httpOnly"] = True
            if c.secure:
                cookie_dict["secure"] = True
            if c.same_site:
                cookie_dict["sameSite"] = c.same_site
            cdp_cookies.append(cookie_dict)
        return cdp_cookies
    except Exception as e:
        logger.warning("Failed to load cookies for %s/%s: %s", user_id, domain, e)
        return []


async def _save_cookies_from_cdp(
    cookie_dao: BrowserCookieDAO,
    user_id: str,
    cdp_cookies: list[dict[str, Any]],
) -> int:
    """Save cookies from CDP getAllCookies response to the database."""
    from app.models.domain import BrowserCookie

    domain_cookies = []
    for c in cdp_cookies:
        expires = None
        if c.get("expires") and c["expires"] > 0:
            try:
                expires = datetime.fromtimestamp(c["expires"])
            except (ValueError, OSError):
                pass

        domain_cookies.append(
            BrowserCookie(
                user_id=user_id,
                domain=c.get("domain", ""),
                name=c.get("name", ""),
                value=c.get("value", ""),
                path=c.get("path", "/"),
                expires=expires,
                http_only=c.get("httpOnly", False),
                secure=c.get("secure", False),
                same_site=c.get("sameSite"),
            )
        )

    if not domain_cookies:
        return 0

    try:
        return await cookie_dao.upsert_cookies(user_id, domain_cookies)
    except Exception as e:
        logger.warning("Failed to save cookies for %s: %s", user_id, e)
        return 0


# ---------------------------------------------------------------------------
# Progress notification helper
# ---------------------------------------------------------------------------


def _notify_user_live_view(url: str) -> None:
    """Queue a Telegram progress message with the browser live-view URL.

    Uses the send_progress module's queue so the message is delivered to the
    user alongside other progress updates.  If no progress callback is set
    (e.g. during tests or cron tasks), this is a silent no-op.
    """
    try:
        from app.tools.send_progress import _queue_progress_message

        _queue_progress_message(f"🖥 Browser session started — watch live: {url}")
    except Exception as exc:
        logger.debug("Could not queue live-view notification: %s", exc)


# ---------------------------------------------------------------------------
# MordecaiBrowser — AgentCoreBrowser subclass with cookie persistence
# ---------------------------------------------------------------------------

# Per-user browser instance cache
_browser_cache: dict[str, "MordecaiBrowser"] = {}


class _LiveViewAgentCoreBrowser:
    """Patches an AgentCoreBrowser delegate to capture BrowserClient instances.

    The upstream ``AgentCoreBrowser.create_browser_session()`` creates a
    ``BrowserClient`` as a local variable and never stores it, so there is
    no way to call ``generate_live_view_url()`` afterwards.  This class
    monkey-patches the delegate's ``create_browser_session`` to intercept
    the client and log a live-view URL for debugging.
    """

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self._session_clients: dict[str, Any] = {}
        # Patch the delegate's create_browser_session
        self._original_create = delegate.create_browser_session
        delegate.create_browser_session = self._create_browser_session_with_live_view

    async def _create_browser_session_with_live_view(self) -> Any:
        """Wrap create_browser_session to capture the client and log live view URL."""
        from bedrock_agentcore.tools.browser_client import BrowserClient as AgentCoreBrowserClient

        if not self._delegate._playwright:
            raise RuntimeError("Playwright not initialized")

        session_client = AgentCoreBrowserClient(region=self._delegate.region)
        session_id = session_client.start(
            identifier=self._delegate.identifier,
            session_timeout_seconds=self._delegate.session_timeout,
        )

        # Store the client so we can generate live view URLs later
        self._session_clients[session_id] = session_client
        # Also store in the delegate's _client_dict for proper cleanup
        self._delegate._client_dict[session_id] = session_client

        logger.info("Started AgentCore browser session: %s", session_id)

        try:
            # Check if live-view stream is available before generating the URL.
            # The system browser (aws.browser.v1) may not support live view.
            session_info = session_client.get_session()
            streams = session_info.get("streams") or {}
            live_stream = streams.get("liveViewStream") or {}
            if live_stream.get("streamStatus") == "ENABLED":
                live_url = session_client.generate_live_view_url()
                logger.info("Browser live view URL (expires in 5 min): %s", live_url)
                _notify_user_live_view(live_url)
            else:
                logger.info(
                    "Live view not available for this browser (stream=%s)",
                    live_stream.get("streamStatus", "N/A"),
                )
        except Exception as e:
            logger.warning("Could not generate live view URL: %s", e)

        cdp_url, cdp_headers = session_client.generate_ws_headers()
        browser = await self._delegate._playwright.chromium.connect_over_cdp(
            endpoint_url=cdp_url, headers=cdp_headers,
        )
        return browser

    def get_live_view_url(self, session_id: str | None = None) -> str | None:
        """Get a live view URL for a session.

        Args:
            session_id: Specific session. If None, uses the most recent one.

        Returns:
            Pre-signed URL (valid ~5 min) or None if unavailable.
        """
        if session_id and session_id in self._session_clients:
            client = self._session_clients[session_id]
        elif self._session_clients:
            client = list(self._session_clients.values())[-1]
        else:
            return None

        try:
            return client.generate_live_view_url()
        except Exception as e:
            logger.warning("Could not generate live view URL: %s", e)
            return None


class MordecaiBrowser:
    """Wrapper around AgentCoreBrowser with live-view URL logging.

    The Strands Agent receives ``browser_instance.browser`` (the @tool
    decorated method) as a tool.  When a browser session starts, the
    live-view URL is logged at INFO level so operators can watch the
    session in real time.
    """

    def __init__(
        self,
        *,
        region: str,
        user_id: str,
        cookie_dao: BrowserCookieDAO,
    ) -> None:
        from strands_tools.browser import AgentCoreBrowser

        self._user_id = user_id
        self._cookie_dao = cookie_dao
        self._delegate = AgentCoreBrowser(region=region)
        self._live_view = _LiveViewAgentCoreBrowser(self._delegate)

    @property
    def browser(self):
        """Return the @tool-decorated method the Strands Agent will invoke."""
        return self._delegate.browser

    def get_live_view_url(self) -> str | None:
        """Get a fresh live-view URL for the most recent browser session."""
        return self._live_view.get_live_view_url()


def get_or_create_browser(
    user_id: str,
    config: AgentConfig,
    cookie_dao: BrowserCookieDAO,
) -> MordecaiBrowser:
    """Get or create a MordecaiBrowser for the given user.

    Instances are cached per user_id so repeated agent creations
    reuse the same browser object.
    """
    if user_id in _browser_cache:
        return _browser_cache[user_id]

    region = getattr(config, "browser_region", "us-west-2")
    instance = MordecaiBrowser(
        region=region,
        user_id=user_id,
        cookie_dao=cookie_dao,
    )
    _browser_cache[user_id] = instance
    logger.info("Created MordecaiBrowser for user %s (region=%s)", user_id, region)
    return instance
