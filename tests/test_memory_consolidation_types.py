import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.memory import MemoryStore
from nanobot.providers.base import LLMResponse, ToolCallRequest


def _make_session(message_count: int = 30, memory_window: int = 50):
    session = MagicMock()
    session.messages = [
        {"role": "user", "content": f"msg{i}", "timestamp": "2026-01-01 00:00"}
        for i in range(message_count)
    ]
    session.last_consolidated = 0
    return session


def _make_tool_response(history_entry, memory_update):
    return LLMResponse(
        content=None,
        tool_calls=[
            ToolCallRequest(
                id="call_1",
                name="save_memory",
                arguments={"history_entry": history_entry, "memory_update": memory_update},
            )
        ],
    )


class TestMemoryConsolidationTypeHandling:
    @pytest.mark.asyncio
    async def test_string_arguments_work(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        provider = AsyncMock()
        provider.chat = AsyncMock(
            return_value=_make_tool_response(
                history_entry="[2026-01-01] User discussed testing.",
                memory_update="# Memory\nUser likes testing.",
            )
        )
        session = _make_session(message_count=60)
        result = await store.consolidate(session, provider, "test-model", memory_window=50)
        assert result is True
        assert store.history_path.exists()
        assert "[2026-01-01] User discussed testing." in store.history_path.read_text()
        assert "User likes testing." in store.memory_path.read_text()

    @pytest.mark.asyncio
    async def test_dict_arguments_serialized_to_json(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        provider = AsyncMock()
        provider.chat = AsyncMock(
            return_value=_make_tool_response(
                history_entry={"timestamp": "2026-01-01", "summary": "User discussed testing."},
                memory_update={"facts": ["User likes testing"], "topics": ["testing"]},
            )
        )
        session = _make_session(message_count=60)
        result = await store.consolidate(session, provider, "test-model", memory_window=50)
        assert result is True
        assert store.history_path.exists()
        history_content = store.history_path.read_text()
        parsed = json.loads(history_content.strip())
        assert parsed["summary"] == "User discussed testing."
        memory_content = store.memory_path.read_text()
        parsed_mem = json.loads(memory_content)
        assert "User likes testing" in parsed_mem["facts"]

    @pytest.mark.asyncio
    async def test_string_arguments_as_raw_json(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        provider = AsyncMock()
        response = LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="save_memory",
                    arguments=json.dumps(
                        {
                            "history_entry": "[2026-01-01] User discussed testing.",
                            "memory_update": "# Memory\nUser likes testing.",
                        }
                    ),
                )
            ],
        )
        provider.chat = AsyncMock(return_value=response)
        session = _make_session(message_count=60)
        result = await store.consolidate(session, provider, "test-model", memory_window=50)
        assert result is True
        assert "User discussed testing." in store.history_path.read_text()

    @pytest.mark.asyncio
    async def test_no_tool_call_returns_false(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        provider = AsyncMock()
        provider.chat = AsyncMock(
            return_value=LLMResponse(content="I summarized the conversation.", tool_calls=[])
        )
        session = _make_session(message_count=60)
        result = await store.consolidate(session, provider, "test-model", memory_window=50)
        assert result is False
        assert not store.history_path.exists()

    @pytest.mark.asyncio
    async def test_skips_when_few_messages(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        provider = AsyncMock()
        session = _make_session(message_count=10)
        result = await store.consolidate(session, provider, "test-model", memory_window=50)
        assert result is True
        provider.chat.assert_not_called()
