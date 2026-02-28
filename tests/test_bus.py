"""Tests for bus/queue.py — MessageBus."""

import asyncio

import pytest

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus


class TestMessageBus:
    @pytest.fixture
    def bus(self):
        return MessageBus()

    @pytest.mark.asyncio
    async def test_publish_and_consume_inbound(self, bus):
        msg = InboundMessage(channel="slack", sender_id="u1", chat_id="c1", content="hello")
        await bus.publish_inbound(msg)
        received = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        assert received.content == "hello"

    @pytest.mark.asyncio
    async def test_publish_and_consume_outbound(self, bus):
        msg = OutboundMessage(channel="slack", chat_id="c1", content="response")
        await bus.publish_outbound(msg)
        received = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        assert received.content == "response"

    @pytest.mark.asyncio
    async def test_multiple_outbound_messages_ordered(self, bus):
        await bus.publish_outbound(OutboundMessage(channel="x", chat_id="c", content="first"))
        await bus.publish_outbound(OutboundMessage(channel="x", chat_id="c", content="second"))

        m1 = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        m2 = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        assert m1.content == "first"
        assert m2.content == "second"

    @pytest.mark.asyncio
    async def test_multiple_inbound_messages_ordered(self, bus):
        for i in range(3):
            await bus.publish_inbound(
                InboundMessage(channel="x", sender_id="u", chat_id="c", content=f"msg{i}")
            )
        msgs = []
        for _ in range(3):
            msgs.append(await asyncio.wait_for(bus.consume_inbound(), timeout=1.0))
        contents = [m.content for m in msgs]
        assert contents == ["msg0", "msg1", "msg2"]


class TestBusEvents:
    def test_inbound_message_defaults(self):
        msg = InboundMessage(channel="x", sender_id="u", chat_id="c", content="hi")
        assert msg.channel == "x"
        assert msg.sender_id == "u"
        assert msg.chat_id == "c"
        assert msg.content == "hi"

    def test_outbound_message_defaults(self):
        msg = OutboundMessage(channel="x", chat_id="c", content="hi")
        assert msg.channel == "x"
        assert msg.chat_id == "c"
        assert msg.content == "hi"
        assert msg.media == []

    def test_inbound_message_metadata(self):
        msg = InboundMessage(
            channel="x", sender_id="u", chat_id="c", content="hi", metadata={"thread_ts": "123"}
        )
        assert msg.metadata.get("thread_ts") == "123"

    def test_outbound_message_media(self):
        msg = OutboundMessage(channel="x", chat_id="c", content="hi", media=["/tmp/img.png"])
        assert "/tmp/img.png" in msg.media
