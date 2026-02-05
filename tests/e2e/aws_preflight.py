"""Helpers for opt-in AWS end-to-end tests.

These tests are meant to be run interactively and may incur costs.

When AWS credentials are missing/expired/invalid, we prefer to **skip** early
with an actionable message, instead of letting downstream services produce
noisy stack traces.
"""

from __future__ import annotations

import os

import pytest


def _env_flag(name: str) -> str:
    return "<set>" if os.environ.get(name) else "<unset>"


def aws_env_summary() -> dict[str, str]:
    # Do NOT include any secret values, only set/unset status.
    keys = [
        "AWS_PROFILE",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
    ]
    return {k: _env_flag(k) for k in keys}


def mask_access_key_id(access_key_id: str | None) -> str:
    if not access_key_id:
        return "<unset>"
    s = str(access_key_id)
    if len(s) <= 4:
        return "<set>"
    return f"<set:...{s[-4:]}>"


def skip_if_aws_auth_invalid(
    *,
    region_name: str | None = None,
    aws_access_key_id: str | None = None,
    aws_secret_access_key: str | None = None,
    aws_session_token: str | None = None,
) -> None:
    """Skip the current test if AWS auth looks invalid.

    We validate credentials by calling STS GetCallerIdentity, which is fast and
    requires no account-specific setup.

    Args:
        region_name: Optional region for the STS client.
    """

    # botocore is a dependency of bedrock-agentcore.
    from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
    from botocore.session import get_session

    region = (
        region_name
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )

    session = get_session()

    using_explicit_args = bool(aws_access_key_id or aws_secret_access_key or aws_session_token)
    try:
        sts = session.create_client(
            "sts",
            region_name=region,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_session_token=aws_session_token,
        )
    except (BotoCoreError, Exception) as e:
        pytest.skip(
            "Unable to create AWS STS client for e2e tests. "
            f"Error: {e}. Env: {aws_env_summary()}. "
            "Using explicit AWS creds args: "
            f"{using_explicit_args} (access_key_id={mask_access_key_id(aws_access_key_id)})"
        )

    try:
        sts.get_caller_identity()
    except BotoCoreError as e:
        # Network/DNS/proxy/etc. When these e2e tests are run interactively,
        # it's much more helpful to *skip* with guidance than to fail with a
        # long stack trace.
        pytest.skip(
            "Unable to reach AWS STS endpoint for e2e tests (network/DNS issue). "
            "Check internet/VPN/proxy/DNS and re-run. "
            f"Region: {region}. Error: {e}. Env: {aws_env_summary()}. "
            "Using explicit AWS creds args: "
            f"{using_explicit_args} (access_key_id={mask_access_key_id(aws_access_key_id)})"
        )
    except OSError as e:
        pytest.skip(
            "Unable to reach AWS STS endpoint for e2e tests (OS/network error). "
            "Check internet/VPN/proxy/DNS and re-run. "
            f"Region: {region}. Error: {e}. Env: {aws_env_summary()}. "
            "Using explicit AWS creds args: "
            f"{using_explicit_args} (access_key_id={mask_access_key_id(aws_access_key_id)})"
        )
    except NoCredentialsError:
        pytest.skip(
            "No AWS credentials found for e2e tests. "
            "Provide credentials (e.g. set AWS_PROFILE or AWS_* env vars). "
            f"Env: {aws_env_summary()}. "
            "Using explicit AWS creds args: "
            f"{using_explicit_args} (access_key_id={mask_access_key_id(aws_access_key_id)})"
        )
    except ClientError as e:
        code = ((e.response or {}).get("Error") or {}).get("Code") or ""
        msg = ((e.response or {}).get("Error") or {}).get("Message") or str(e)

        # Common credential failures.
        if (
            code
            in {
                "UnrecognizedClientException",
                "InvalidClientTokenId",
                "ExpiredToken",
                "ExpiredTokenException",
            }
            or "security token" in msg.lower()
        ):
            pytest.skip(
                "AWS credentials are expired/invalid for e2e tests. "
                "Refresh AWS auth (e.g. `aws sso login`) and re-run. "
                "Note: your environment currently has AWS_* env vars set; "
                "if they are stale, unset them and use AWS_PROFILE instead. "
                f"Env: {aws_env_summary()}. "
                "Using explicit AWS creds args: "
                f"{using_explicit_args} (access_key_id={mask_access_key_id(aws_access_key_id)}). "
                f"Original error: {code}: {msg}"
            )

        # Permissions / config issues: also skip, because these tests are opt-in.
        if code in {"AccessDenied", "AccessDeniedException"}:
            pytest.skip(
                "AWS credentials are present but lack permission for STS:GetCallerIdentity. "
                "Fix the AWS principal/policies used for e2e tests and re-run. "
                f"Env: {aws_env_summary()}. "
                "Using explicit AWS creds args: "
                f"{using_explicit_args} (access_key_id={mask_access_key_id(aws_access_key_id)}). "
                f"Original error: {code}: {msg}"
            )

        # Unknown ClientError: surface it.
        raise


def require_real_tests_enabled(*, allow_legacy_aws_flag: bool = False) -> None:
    """Skip unless the caller explicitly enabled real (non-mocked) tests."""

    if os.environ.get("MORDECAI_RUN_REAL_TESTS") == "1":
        return

    if allow_legacy_aws_flag and os.environ.get("MORDECAI_RUN_E2E_AWS") == "1":
        return

    if allow_legacy_aws_flag:
        pytest.skip(
            "Set MORDECAI_RUN_REAL_TESTS=1 (or MORDECAI_RUN_E2E_AWS=1) to run real AWS/LLM tests"
        )

    pytest.skip("Set MORDECAI_RUN_REAL_TESTS=1 to run real AWS/LLM tests")
