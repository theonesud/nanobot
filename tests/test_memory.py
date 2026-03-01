from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.memory import MemoryStore


class TestMemoryStore:
    @pytest.fixture
    def memory_store(self, temp_workspace):
        return MemoryStore(temp_workspace)

    def test_initialization(self, memory_store, temp_workspace):
        assert memory_store.memory_path.parent == temp_workspace / "memory"
        assert memory_store.memory_path == temp_workspace / "memory" / "MEMORY.md"
        assert memory_store.history_path == temp_workspace / "memory" / "HISTORY.md"

    def test_read_long_term_empty(self, memory_store):
        content = memory_store.read_long_term()
        assert content == ""

    def test_read_long_term_with_content(self, memory_store):
        memory_store.write_long_term("# Important Facts\n- User prefers dark mode")
        content = memory_store.read_long_term()
        assert content == "# Important Facts\n- User prefers dark mode"

    def test_write_long_term(self, memory_store):
        content = "# Test Memory\nTest content"
        memory_store.write_long_term(content)
        assert memory_store.memory_path.read_text() == content

    def test_append_history(self, memory_store):
        entry = "[2026-02-28 14:00] USER: Hello, world!"
        memory_store.append_history(entry)
        history_content = memory_store.history_path.read_text()
        assert entry in history_content

    def test_append_history_multiple(self, memory_store):
        entries = ["[2026-02-28 14:00] USER: Hello", "[2026-02-28 14:01] ASSISTANT: Hi there!"]
        for entry in entries:
            memory_store.append_history(entry)
        history = memory_store.history_path.read_text()
        assert "USER: Hello" in history
        assert "ASSISTANT: Hi there!" in history

    def test_get_memory_context_empty(self, memory_store):
        context = memory_store.get_memory_context()
        assert context == ""

    def test_get_memory_context_with_content(self, memory_store):
        memory_store.write_long_term("User name is Alice")
        context = memory_store.get_memory_context()
        assert "User name is Alice" in context
        assert "Long-term Memory" in context

    @pytest.mark.asyncio
    async def test_consolidate_no_messages(self, memory_store, mock_session, mock_provider):
        mock_session.messages = []
        result = await memory_store.consolidate(mock_session, mock_provider, "gpt-4")
        assert result is True

    @pytest.mark.asyncio
    async def test_consolidate_few_messages(self, memory_store, mock_session, mock_provider):
        mock_session.messages = [
            {"role": "user", "content": "Hello", "timestamp": "2026-02-28T10:00:00"}
        ]
        result = await memory_store.consolidate(mock_session, mock_provider, "gpt-4")
        assert result is True

    @pytest.mark.asyncio
    async def test_consolidate_success(self, memory_store, mock_session, mock_provider):
        mock_session.messages = [
            {
                "role": "user",
                "content": f"Message {i}",
                "timestamp": f"2026-02-28T10:{i:02d}:00",
                "tools_used": [],
            }
            for i in range(60)
        ]
        mock_session.last_consolidated = 10
        mock_response = MagicMock()
        mock_response.has_tool_calls = True
        mock_response.tool_calls = [
            MagicMock(
                arguments={
                    "history_entry": "[2026-02-28 10:00] User exchanged messages",
                    "memory_update": "# Updated Memory\n- User is active",
                }
            )
        ]
        mock_provider.chat = AsyncMock(return_value=mock_response)
        result = await memory_store.consolidate(mock_session, mock_provider, "gpt-4")
        assert result is True
        assert "User exchanged messages" in memory_store.history_path.read_text()
        assert "User is active" in memory_store.memory_path.read_text()

    @pytest.mark.asyncio
    async def test_consolidate_no_tool_call(self, memory_store, mock_session, mock_provider):
        mock_session.messages = [
            {
                "role": "user",
                "content": f"Message {i}",
                "timestamp": f"2026-02-28T10:{i:02d}:00",
                "tools_used": [],
            }
            for i in range(60)
        ]
        mock_response = MagicMock()
        mock_response.has_tool_calls = False
        mock_provider.chat = AsyncMock(return_value=mock_response)
        result = await memory_store.consolidate(mock_session, mock_provider, "gpt-4")
        assert result is False

    @pytest.mark.asyncio
    async def test_consolidate_exception(self, memory_store, mock_session, mock_provider):
        mock_session.messages = [
            {
                "role": "user",
                "content": f"Message {i}",
                "timestamp": f"2026-02-28T10:{i:02d}:00",
                "tools_used": [],
            }
            for i in range(60)
        ]
        mock_provider.chat = AsyncMock(side_effect=Exception("API Error"))
        result = await memory_store.consolidate(mock_session, mock_provider, "gpt-4")
        assert result is False

    @pytest.mark.asyncio
    async def test_consolidate_archive_all(self, memory_store, mock_session, mock_provider):
        mock_session.messages = [
            {
                "role": "user",
                "content": "Message 1",
                "timestamp": "2026-02-28T10:00:00",
                "tools_used": ["read"],
            },
            {
                "role": "assistant",
                "content": "Response 1",
                "timestamp": "2026-02-28T10:00:01",
                "tools_used": [],
            },
        ]
        mock_response = MagicMock()
        mock_response.has_tool_calls = True
        mock_response.tool_calls = [
            MagicMock(
                arguments={
                    "history_entry": "[2026-02-28] Conversation archived",
                    "memory_update": "# Memory\nAll archived",
                }
            )
        ]
        mock_provider.chat = AsyncMock(return_value=mock_response)
        result = await memory_store.consolidate(
            mock_session, mock_provider, "gpt-4", archive_all=True
        )
        assert result is True
