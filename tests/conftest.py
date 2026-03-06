import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.config.schema import Config


@pytest.fixture
def temp_workspace():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_config():
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
    provider = AsyncMock()
    provider.chat = AsyncMock()
    return provider


@pytest.fixture
def mock_session():
    session = MagicMock()
    session.messages = [
        {"role": "user", "content": "Hello", "timestamp": "2026-02-28T10:00:00"},
        {
            "role": "assistant",
            "content": "Hi there!",
            "timestamp": "2026-02-28T10:00:01",
            "tools_used": [],
        },
    ]
    session.last_consolidated = 0
    return session
