"""1Password credential retrieval tool using the Python SDK.

Provides a ``get_credential`` tool that the agent can use to fetch credentials
from 1Password via a Service Account Token (``OP_SERVICE_ACCOUNT_TOKEN``).
No desktop app or biometric approval is required — the token provides
headless access to secrets in the configured vault.

Context isolation follows the same pattern as ``skill_secrets.py``:
module-level state is set before each agent invocation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

try:
    from onepassword.client import Client  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    Client = None  # type: ignore[assignment,misc]

try:
    from strands import tool  # type: ignore[import-not-found]
except Exception:  # pragma: no cover

    def tool(*_args, **_kwargs):
        def _decorator(fn):
            return fn

        return _decorator


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level context (set per-message by agent_creation.py)
# ---------------------------------------------------------------------------

_current_user_id: str | None = None
_config: Any = None

# Cached SDK client — re-authenticated only when the token changes.
_cached_client: Any = None
_cached_token: str | None = None


def set_credential_context(
    *,
    user_id: str,
    config: Any = None,
) -> None:
    """Set credential tool context before agent invocation.

    Args:
        user_id: Current user's ID.
        config: AgentConfig instance.
    """
    global _current_user_id, _config
    _current_user_id = user_id
    _config = config


async def _get_op_client() -> Any:
    """Return an authenticated 1Password SDK client.

    Caches the client at module level and only re-authenticates when the
    ``OP_SERVICE_ACCOUNT_TOKEN`` env var changes.

    Raises:
        RuntimeError: If the token is not set or the SDK is not installed.
    """
    global _cached_client, _cached_token

    if Client is None:
        raise RuntimeError(
            "The onepassword-sdk package is not installed. "
            "Install with: pip install onepassword-sdk"
        )

    token = os.environ.get("OP_SERVICE_ACCOUNT_TOKEN")
    if not token:
        raise RuntimeError(
            "OP_SERVICE_ACCOUNT_TOKEN is not set. "
            "Ask the user for their 1Password Service Account Token "
            "and store it using set_skill_env_vars(skill_name='onepassword', "
            "env_json='{\"OP_SERVICE_ACCOUNT_TOKEN\": \"<token>\"}')."
        )

    if _cached_client is None or token != _cached_token:
        _cached_client = await Client.authenticate(
            auth=token,
            integration_name="Mordecai",
            integration_version="v1.0.0",
        )
        _cached_token = token

    return _cached_client


async def _resolve_secret(secret_ref: str) -> str:
    """Resolve a single ``op://`` secret reference via the SDK.

    Raises:
        RuntimeError: If authentication or resolution fails.
    """
    client = await _get_op_client()
    return await client.secrets.resolve(secret_ref)


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from synchronous code.

    Handles the case where an event loop is already running (e.g. inside
    an async framework) by creating a new loop in a thread.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # We're inside an existing event loop — run in a new thread.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)


@tool(
    name="get_credential",
    description=(
        "Retrieve credentials from 1Password for a specific service. "
        "Uses the 1Password Python SDK with a Service Account Token. "
        "Parameters:\n"
        "- service_name: The name of the 1Password item to look up "
        "(e.g., 'Outlook Work', 'GitHub Token').\n"
        "- vault: Optional vault name to search in (default: uses the "
        "configured default vault).\n"
        "- fields: Comma-separated list of fields to retrieve "
        "(default: 'username,password'). Supports 'otp' for one-time passwords.\n\n"
        "⚠️ OTP CODES ARE TIME-SENSITIVE (valid ~30 seconds). "
        "Fetch the OTP as late as possible — ideally right before it is needed. "
        "If a browser login requires OTP/MFA, fetch username+password first, "
        "then fetch 'otp' in a SEPARATE call immediately before passing it to "
        "browse_web. If browse_web reports an OTP_EXPIRED error, call "
        "get_credential again with fields='otp' to get a fresh code and retry."
    ),
)
def get_credential(
    service_name: str,
    vault: str | None = None,
    fields: str = "username,password",
) -> str:
    """Retrieve credentials from 1Password using the Python SDK.

    Args:
        service_name: Name of the 1Password item.
        vault: Optional vault name.
        fields: Comma-separated list of fields to retrieve.

    Returns:
        JSON string with the requested credential fields, or an error message.
    """
    t0 = time.perf_counter()

    if not _current_user_id:
        return "Credential tool error: user context not set."

    if not service_name or not service_name.strip():
        return "Credential tool error: service_name is required."

    # Check token availability early for a clear error message.
    if not os.environ.get("OP_SERVICE_ACCOUNT_TOKEN"):
        return (
            "Credential tool error: OP_SERVICE_ACCOUNT_TOKEN is not set. "
            "Ask the user for their 1Password Service Account Token "
            "(starts with 'ops_') and store it with set_skill_env_vars."
        )

    vault_name = vault or os.environ.get("OP_DEFAULT_VAULT", "Private")
    requested_fields = [f.strip() for f in fields.split(",") if f.strip()]
    result_data: dict[str, str] = {}

    for field_name in requested_fields:
        # Build the op:// secret reference
        if field_name.lower() == "otp":
            secret_ref = (
                f"op://{vault_name}/{service_name}/"
                f"one-time password?attribute=otp"
            )
        else:
            secret_ref = f"op://{vault_name}/{service_name}/{field_name}"

        try:
            value = _run_async(_resolve_secret(secret_ref))
            result_data[field_name] = value
        except RuntimeError as e:
            logger.warning(
                "Failed to resolve 1Password field %s for %s: %s",
                field_name,
                service_name,
                e,
            )
            result_data[field_name] = f"<error: {e}>"
        except Exception as e:
            logger.warning(
                "Unexpected error resolving 1Password field %s for %s: %s",
                field_name,
                service_name,
                e,
            )
            result_data[field_name] = f"<error: {e}>"

    duration_ms = int((time.perf_counter() - t0) * 1000)
    logger.info(
        "get_credential completed for user %s in %dms (service=%s, fields=%s)",
        _current_user_id,
        duration_ms,
        service_name,
        fields,
    )

    # Return as JSON — never log the actual values
    return json.dumps(result_data)
