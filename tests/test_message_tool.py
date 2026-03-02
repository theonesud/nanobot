import pytest

from nanobot.agent.tools.message import MessageTool


@pytest.mark.asyncio
async def test_message_tool_returns_error_when_no_target_context() -> None:
    tool = MessageTool()
    result = await tool.execute(content="test")
    assert result == "Error: No target channel/chat specified"

@pytest.mark.asyncio
async def test_message_tool_success() -> None:
    from unittest.mock import AsyncMock
    callback = AsyncMock()
    tool = MessageTool(send_callback=callback)
    tool.set_context(channel="slack", chat_id="C1")

    result = await tool.execute(content="hello")
    assert "Message sent" in result
    callback.assert_called_once()
    msg = callback.call_args[0][0]
    assert msg.content == "hello"
    assert msg.channel == "slack"

@pytest.mark.asyncio
async def test_message_tool_with_media() -> None:
    from unittest.mock import AsyncMock
    callback = AsyncMock()
    tool = MessageTool(send_callback=callback)
    tool.set_context(channel="tg", chat_id="U1")

    result = await tool.execute(content="with photo", media=["path/to/img.jpg"])
    assert "1 attachments" in result
    msg = callback.call_args[0][0]
    assert msg.media == ["path/to/img.jpg"]

@pytest.mark.asyncio
async def test_message_tool_no_callback() -> None:
    tool = MessageTool()
    tool.set_context(channel="x", chat_id="y")
    result = await tool.execute(content="fail")
    assert "not configured" in result
