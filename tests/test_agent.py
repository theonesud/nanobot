from unittest.mock import MagicMock

import pytest

from nanobot.agent.auditor import CommandAuditor
from nanobot.agent.context import ContextBuilder
from nanobot.agent.tools.registry import ToolRegistry


class TestAuditor:
    @pytest.fixture
    def auditor(self):
        provider = MagicMock()
        return CommandAuditor(provider=provider, model="opencode")

    def test_auditor_initialization(self, auditor):
        assert auditor.model == "opencode"

    @pytest.mark.asyncio
    async def test_audit_request(self, auditor):
        assert hasattr(auditor, "evaluate")


class TestContext:
    @pytest.fixture
    def context(self, temp_workspace):
        return ContextBuilder(workspace=temp_workspace)

    def test_context_initialization(self, context, temp_workspace):
        assert context.workspace == temp_workspace

    def test_context_build_messages(self, context):
        msgs = context.build_messages([], "Hello", channel="cli", chat_id="direct")
        assert len(msgs) >= 2
        assert any((m["role"] == "user" and "Hello" in str(m["content"]) for m in msgs))


class TestToolRegistry:
    def test_registry_creation(self):
        registry = ToolRegistry()
        assert registry is not None
        assert len(registry.tool_names) == 0

    def test_registry_register(self):
        registry = ToolRegistry()

        def test_tool():
            pass

        test_tool.name = "test_tool"
        test_tool.description = "A test tool"
        registry.register(test_tool)
        tools = registry.tool_names
        assert "test_tool" in tools

    def test_registry_get(self):
        registry = ToolRegistry()

        def test_tool():
            pass

        test_tool.name = "get_test"
        test_tool.description = "A test tool"
        registry.register(test_tool)
        tool = registry.get("get_test")
        assert tool is not None
