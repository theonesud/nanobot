from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.tools.spawn import SpawnTool


class TestSpawnTool:
    @pytest.mark.asyncio
    async def test_spawn_calls_manager(self):
        mock_manager = MagicMock()
        mock_manager.spawn = AsyncMock(return_value="Agent started")

        tool = SpawnTool(manager=mock_manager)
        tool.set_context(channel="slack", chat_id="C123", thread_ts="123.456")

        result = await tool.execute(task="do something", label="task1")

        assert result == "Agent started"
        mock_manager.spawn.assert_called_once_with(
            task="do something",
            label="task1",
            origin_channel="slack",
            origin_chat_id="C123",
            session_key="slack:C123",
            thread_ts="123.456",
            on_progress=None
        )

    def test_spawn_tool_properties(self):
        tool = SpawnTool(manager=MagicMock())
        assert tool.name == "spawn"
        assert "task" in tool.parameters["required"]
