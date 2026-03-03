from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop, SkillsLoader
from nanobot.agent.tools import ToolRegistry, register_builtin_tools
from nanobot.bus.events import InboundMessage, OutboundMessage


@pytest.fixture
def ws(tmp_path):
    return tmp_path


@pytest.fixture
def bus():
    b = MagicMock()
    b.publish_outbound = AsyncMock()
    b.consume_inbound = AsyncMock()
    return b


@pytest.fixture
def provider():
    p = MagicMock()
    p.get_default_model.return_value = "test-model"
    p.chat = AsyncMock(
        return_value=MagicMock(content="Hello!", has_tool_calls=False, usage={}, error=None)
    )
    return p


def test_tool_registry(ws):
    reg = ToolRegistry(ws)

    async def t_fn(x, **k):
        return f"res:{x}"

    reg.add("test_tool", "desc", {"type": "object", "properties": {"x": {"type": "string"}}}, t_fn)
    assert "test_tool" in reg.tools
    assert len(reg.get_definitions()) == 1


@pytest.mark.asyncio
async def test_agent_loop_basic(bus, provider, ws):
    loop = AgentLoop(bus, provider, ws)
    msg = InboundMessage("cli", "u1", "c1", "Hello")
    res = await loop._process(msg)
    assert isinstance(res, OutboundMessage)
    assert "Hello!" in res.content
    provider.chat.assert_called()


@pytest.mark.asyncio
async def test_skills_loader(ws):
    s_dir = ws / "skills" / "test_skill"
    s_dir.mkdir(parents=True)
    (s_dir / "SKILL.md").write_text("Skill content")
    loader = SkillsLoader(ws)
    skills = loader.list_skills()
    s = next(s for s in skills if s["name"] == "test_skill")
    assert s["path"].read_text() == "Skill content"


@pytest.mark.asyncio
async def test_builtin_tools_registration(ws):
    reg = ToolRegistry(ws)
    register_builtin_tools(reg, ws)
    assert "read_file" in reg.tools
    assert "write_file" in reg.tools
    assert "exec" in reg.tools
