"""Tests for agent components - auditor, context, tools."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from nanobot.agent.auditor import CommandAuditor
from nanobot.agent.context import ContextBuilder


class TestAuditor:
    """Tests for the Auditor agent."""

    @pytest.fixture
    def auditor(self):
        """Create an auditor instance."""
        return CommandAuditor(bin_path="opencode")

    def test_auditor_initialization(self, auditor):
        """Test auditor initializes correctly."""
        assert auditor.bin_path == "opencode"

    @pytest.mark.asyncio
    async def test_audit_request(self, auditor):
        """Test auditing a request."""
        assert hasattr(auditor, "evaluate")


class TestContext:
    """Tests for agent Context."""

    @pytest.fixture
    def context(self, temp_workspace):
        """Create a context instance."""
        return ContextBuilder(workspace=temp_workspace)

    def test_context_initialization(self, context, temp_workspace):
        """Test context initializes correctly."""
        assert context.workspace == temp_workspace

    def test_context_build_messages(self, context):
        """Test building messages."""
        msgs = context.build_messages([], "Hello", channel="cli", chat_id="direct")
        assert len(msgs) >= 2
        assert any(m["role"] == "user" and "Hello" in str(m["content"]) for m in msgs)


class TestToolRegistry:
    """Tests for tool registry."""

    def test_registry_creation(self):
        """Test creating a tool registry."""
        from nanobot.agent.tools.registry import ToolRegistry

        registry = ToolRegistry()
        assert registry is not None
        assert len(registry.tool_names) == 0

    def test_registry_register(self):
        """Test registering a tool."""
        from nanobot.agent.tools.registry import ToolRegistry

        registry = ToolRegistry()

        def test_tool():
            pass

        test_tool.name = "test_tool"
        test_tool.description = "A test tool"

        registry.register(test_tool)
        tools = registry.tool_names
        assert "test_tool" in tools

    def test_registry_get(self):
        """Test getting a tool."""
        from nanobot.agent.tools.registry import ToolRegistry

        registry = ToolRegistry()

        def test_tool():
            pass

        test_tool.name = "get_test"
        test_tool.description = "A test tool"

        registry.register(test_tool)
        tool = registry.get("get_test")
        assert tool is not None
