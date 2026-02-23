"""AgentCore Browser replay lookup.

Uses bedrock_agentcore BrowserClient to retrieve the sessionReplayArtifact
for a given session.
"""

from __future__ import annotations

import logging
from typing import Any

from bedrock_agentcore.tools.browser_client import BrowserClient

from app.models.artifacts import S3Location

logger = logging.getLogger(__name__)


class AgentCoreBrowserReplayService:
    def __init__(self, *, region: str, browser_identifier: str) -> None:
        self._region = region
        self._browser_identifier = browser_identifier

    def get_replay_s3_location(self, *, session_id: str) -> S3Location | None:
        """Return the S3 location of a session replay artifact, if available."""

        client = BrowserClient(region=self._region)

        try:
            session = client.get_session(browser_id=self._browser_identifier, session_id=session_id)
        except Exception as exc:
            logger.warning(
                "Failed to fetch AgentCore browser session (identifier=%s session_id=%s): %s",
                self._browser_identifier,
                session_id,
                exc,
            )
            return None

        artifact = session.get("sessionReplayArtifact")
        loc = _parse_replay_artifact_location(artifact)
        if loc is None:
            return None

        return loc


def _parse_replay_artifact_location(raw: Any) -> S3Location | None:
    """Best-effort parser for AgentCore sessionReplayArtifact shapes."""

    if not raw:
        return None

    if isinstance(raw, S3Location):
        return raw

    if isinstance(raw, dict):
        # Observed/expected shapes:
        # - {"s3Location": {"bucket": "...", "key": "..."}}
        # - {"bucket": "...", "key": "..."}
        s3_loc = raw.get("s3Location") if "s3Location" in raw else raw
        if isinstance(s3_loc, dict):
            bucket = str(s3_loc.get("bucket") or "").strip()
            key = str(s3_loc.get("key") or "").strip()
            if bucket and key:
                return S3Location(bucket=bucket, key=key)

    return None
