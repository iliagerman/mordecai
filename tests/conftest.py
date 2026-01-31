"""Pytest configuration and fixtures."""

import logging
import os

import pytest
import pytest_asyncio


# Configure pytest-asyncio to auto-detect async tests
pytest_plugins = ("pytest_asyncio",)


def pytest_configure(config: pytest.Config) -> None:
    """Adjust logging for opt-in real/e2e tests.

    The e2e suite intentionally skips when AWS auth is invalid. Botocore logs
    credential resolution at INFO level (e.g. "Found credentials in environment
    variables"), which creates noise for the common skip path.
    """

    if (
        os.environ.get("MORDECAI_RUN_REAL_TESTS") != "1"
        and os.environ.get("MORDECAI_RUN_E2E_AWS") != "1"
    ):
        return

    for name in [
        "botocore.credentials",
        "botocore",
        "boto3",
        "bedrock_agentcore",
    ]:
        logging.getLogger(name).setLevel(logging.WARNING)


@pytest.fixture
def anyio_backend():
    """Use asyncio as the async backend."""
    return "asyncio"
