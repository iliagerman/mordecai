"""Pytest configuration and fixtures."""

import pytest
import pytest_asyncio


# Configure pytest-asyncio to auto-detect async tests
pytest_plugins = ("pytest_asyncio",)


@pytest.fixture
def anyio_backend():
    """Use asyncio as the async backend."""
    return "asyncio"
