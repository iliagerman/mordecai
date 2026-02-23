"""Artifact service (S3 presigned links).

Keeps AWS/S3 logic out of tool modules.
"""

from __future__ import annotations

import logging

import boto3

from app.models.artifacts import PresignedUrl, S3Location

logger = logging.getLogger(__name__)


class ArtifactService:
    def __init__(self, *, region: str) -> None:
        self._region = region
        self._s3 = boto3.client("s3", region_name=region)

    def presign_get_object(self, *, location: S3Location, expires_in_seconds: int) -> PresignedUrl:
        # boto3 clamps/validates expires-in internally; we do a light guard.
        if expires_in_seconds <= 0:
            expires_in_seconds = 60

        url = self._s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": location.bucket, "Key": location.key},
            ExpiresIn=expires_in_seconds,
        )

        logger.info(
            "Generated presigned URL (bucket=%s key=%s ttl=%s)",
            location.bucket,
            location.key,
            expires_in_seconds,
        )
        return PresignedUrl(url=url, expires_in_seconds=expires_in_seconds)
