"""Tests for agent/tools/registry.py — ToolRegistry."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.registry import ToolRegistry


def _make_tool(name: str, description: str = "A tool.", params_required: list[str] | None = None):
    """Create a minimal Tool subclass for testing."""
    _name = name
    _description = description
    _parameters = {
        "type": "object",
        "properties": {f: {"type": "string"} for f in (params_required or [])},
        "required": params_required or [],
    }

    class MockTool(Tool):
        @property
        def name(self) -> str:
            return _name

        @property
        def description(self) -> str:
            return _description

        @property
        def parameters(self) -> dict:
            return _parameters

        async def execute(self, **kwargs: Any) -> str:
            return "ok"

    instance = MockTool()
    instance.execute = AsyncMock(return_value="ok")
    return instance


class TestToolRegistryBasics:
    def test_empty_registry(self):
        reg = ToolRegistry()
        assert len(reg.tool_names) == 0

    def test_register_tool(self):
        reg = ToolRegistry()
        tool = _make_tool("my_tool")
        reg.register(tool)
        assert "my_tool" in reg.tool_names

    def test_register_multiple(self):
        reg = ToolRegistry()
        reg.register(_make_tool("tool_a"))
        reg.register(_make_tool("tool_b"))
        assert "tool_a" in reg.tool_names
        assert "tool_b" in reg.tool_names

    def test_get_existing_tool(self):
        reg = ToolRegistry()
        t = _make_tool("get_me")
        reg.register(t)
        assert reg.get("get_me") is t

    def test_get_missing_returns_none(self):
        reg = ToolRegistry()
        assert reg.get("nonexistent") is None

    def test_has_tool(self):
        reg = ToolRegistry()
        reg.register(_make_tool("exists"))
        assert reg.has("exists") is True
        assert reg.has("missing") is False

    def test_get_definitions_returns_list(self):
        reg = ToolRegistry()
        reg.register(_make_tool("d_tool"))
        defs = reg.get_definitions()
        assert isinstance(defs, list)
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "d_tool"


class TestToolRegistryExecute:
    @pytest.mark.asyncio
    async def test_execute_existing_tool(self):
        reg = ToolRegistry()
        tool = _make_tool("exec_tool")
        tool.execute = AsyncMock(return_value="good result")
        reg.register(tool)
        result = await reg.execute("exec_tool", {})
        assert result == "good result"

    @pytest.mark.asyncio
    async def test_execute_missing_tool_error(self):
        reg = ToolRegistry()
        result = await reg.execute("unknown_tool", {})
        assert "Error" in result
        assert "unknown_tool" in result

    @pytest.mark.asyncio
    async def test_execute_with_params(self):
        reg = ToolRegistry()
        captured = {}

        async def capture(**kwargs):
            captured.update(kwargs)
            return "captured"

        tool = _make_tool("capture_tool", params_required=["text"])
        tool.execute = capture
        reg.register(tool)
        await reg.execute("capture_tool", {"text": "hello"})
        assert captured.get("text") == "hello"

    @pytest.mark.asyncio
    async def test_execute_passes_kwargs(self):
        reg = ToolRegistry()
        captured = {}

        async def capture(**kwargs):
            captured.update(kwargs)
            return "ok"

        tool = _make_tool("kwarg_tool")
        tool.execute = capture
        reg.register(tool)
        await reg.execute("kwarg_tool", {}, channel="slack", chat_id="C123")
        assert captured.get("channel") == "slack"
        assert captured.get("chat_id") == "C123"

    @pytest.mark.asyncio
    async def test_execute_missing_required_param_returns_error(self):
        reg = ToolRegistry()
        tool = _make_tool("required_tool", params_required=["text"])
        reg.register(tool)
        result = await reg.execute("required_tool", {})
        assert "Error" in result
        assert "text" in result

    @pytest.mark.asyncio
    async def test_execute_exception_returns_error(self):
        reg = ToolRegistry()
        tool = _make_tool("crasher")
        tool.execute = AsyncMock(side_effect=RuntimeError("boom"))
        reg.register(tool)
        result = await reg.execute("crasher", {})
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_execute_error_result_appends_hint(self):
        reg = ToolRegistry()
        tool = _make_tool("error_returner")
        tool.execute = AsyncMock(return_value="Error: something went wrong")
        reg.register(tool)
        result = await reg.execute("error_returner", {})
        assert "Error" in result
        assert "Analyze" in result or "approach" in result.lower()

    @pytest.mark.asyncio
    async def test_params_override_kwargs_on_conflict(self):
        """params (tool arguments) should take precedence over kwargs (context)."""
        reg = ToolRegistry()
        captured = {}

        async def capture(**kwargs):
            captured.update(kwargs)
            return "ok"

        tool = _make_tool("conflict_tool")
        tool.execute = capture
        reg.register(tool)
        # Inject channel via params (explicit tool argument) vs. kwargs (context)
        await reg.execute("conflict_tool", {"channel": "from_params"}, channel="from_kwargs")
        assert captured.get("channel") == "from_params"
