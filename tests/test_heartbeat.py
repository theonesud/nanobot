"""Tests for heartbeat service."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

from nanobot.heartbeat.service import HeartbeatService


class TestHeartbeatService:
    """Tests for the HeartbeatService."""

    @pytest.fixture
    def heartbeat_service(self, temp_workspace):
        """Create a heartbeat service instance."""
        provider = MagicMock()
        return HeartbeatService(
            workspace=temp_workspace,
            provider=provider,
            model="test-model",
            interval_s=60,
            enabled=True,
        )

    def test_heartbeat_initialization(self, heartbeat_service, temp_workspace):
        """Test heartbeat service initializes correctly."""
        assert heartbeat_service.workspace == temp_workspace
        assert heartbeat_service._running is False

    @pytest.mark.asyncio
    async def test_heartbeat_start_stop(self, heartbeat_service):
        """Test starting and stopping heartbeat."""
        await heartbeat_service.start()
        assert heartbeat_service._running is True

        heartbeat_service.stop()
        assert heartbeat_service._running is False

    @pytest.mark.asyncio
    async def test_heartbeat_tick_no_file(self, heartbeat_service):
        """Test heartbeat tick behavior when file does not exist."""
        # By default temp_workspace has no HEARTBEAT.md
        await heartbeat_service.start()
        await heartbeat_service._tick()
        assert heartbeat_service._running is True

    @pytest.mark.asyncio
    async def test_heartbeat_multiple_starts(self, heartbeat_service):
        """Test starting heartbeat when already running."""
        await heartbeat_service.start()
        await heartbeat_service.start()  # Should not raise

        heartbeat_service.stop()

    @pytest.mark.asyncio
    async def test_heartbeat_disabled(self, temp_workspace):
        """Test heartbeat with disabled config."""
        provider = MagicMock()
        service = HeartbeatService(
            workspace=temp_workspace,
            provider=provider,
            model="test-model",
            interval_s=60,
            enabled=False,
        )

        # Should not start when disabled
        await service.start()
        assert service._running is False
