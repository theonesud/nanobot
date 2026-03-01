from unittest.mock import MagicMock

import pytest

from nanobot.heartbeat.service import HeartbeatService


class TestHeartbeatService:
    @pytest.fixture
    def heartbeat_service(self, temp_workspace):
        provider = MagicMock()
        return HeartbeatService(
            workspace=temp_workspace,
            provider=provider,
            model="test-model",
            interval_s=60,
            enabled=True,
        )

    def test_heartbeat_initialization(self, heartbeat_service, temp_workspace):
        assert heartbeat_service.workspace == temp_workspace
        assert heartbeat_service._running is False

    @pytest.mark.asyncio
    async def test_heartbeat_start_stop(self, heartbeat_service):
        await heartbeat_service.start()
        assert heartbeat_service._running is True
        heartbeat_service.stop()
        assert heartbeat_service._running is False

    @pytest.mark.asyncio
    async def test_heartbeat_tick_no_file(self, heartbeat_service):
        await heartbeat_service.start()
        await heartbeat_service._tick()
        assert heartbeat_service._running is True

    @pytest.mark.asyncio
    async def test_heartbeat_multiple_starts(self, heartbeat_service):
        await heartbeat_service.start()
        await heartbeat_service.start()
        heartbeat_service.stop()

    @pytest.mark.asyncio
    async def test_heartbeat_disabled(self, temp_workspace):
        provider = MagicMock()
        service = HeartbeatService(
            workspace=temp_workspace,
            provider=provider,
            model="test-model",
            interval_s=60,
            enabled=False,
        )
        await service.start()
        assert service._running is False
