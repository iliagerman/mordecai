"""Artifact and storage models.

These models are used for typed, validated boundaries between services
(e.g., S3 presigning, replay link emission).
"""

from __future__ import annotations

from app.models.base import JsonModel


class S3Location(JsonModel):
    bucket: str
    key: str


class PresignedUrl(JsonModel):
    url: str
    expires_in_seconds: int


class BrowserReplayLink(JsonModel):
    """Replay link information surfaced to the user."""

    session_id: str
    browser_identifier: str
    url: str
    expires_in_seconds: int
