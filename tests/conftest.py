"""Pytest configuration and fixtures."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_config():
    """Create a mock configuration."""
    from nanobot.config.schema import Config

    with tempfile.TemporaryDirectory() as tmpdir:
        config = Config(
            agents__defaults__workspace=tmpdir,
            channels__slack__enabled=False,
            channels__telegram__enabled=False,
            channels__discord__enabled=False,
            channels__email__enabled=False,
        )
        yield config


@pytest.fixture
def mock_provider():
    """Create a mock LLM provider."""
    provider = AsyncMock()
    provider.chat = AsyncMock()
    return provider


@pytest.fixture
def mock_session():
    """Create a mock session."""
    session = MagicMock()
    session.messages = [
        {"role": "user", "content": "Hello", "timestamp": "2026-02-28T10:00:00"},
        {"role": "assistant", "content": "Hi there!", "timestamp": "2026-02-28T10:00:01", "tools_used": []},
    ]
    session.last_consolidated = 0
    return session


@pytest.fixture
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
