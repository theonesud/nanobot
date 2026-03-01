from unittest.mock import MagicMock

import pytest

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import SlackConfig


class TestChannelConfig:
    def test_config_creation(self):
        config = SlackConfig(enabled=True)
        assert config.enabled


class TestMessage:
    def test_message_creation(self):
        msg = InboundMessage(content="Hello", sender_id="user123", channel="slack", chat_id="C123")
        assert msg.content == "Hello"
        assert msg.sender_id == "user123"
        assert msg.channel == "slack"

    def test_outbound_message(self):
        msg = OutboundMessage(content="Hello", channel="slack", chat_id="C123")
        assert msg.chat_id == "C123"


class TestChannel:
    def test_channel_abstract(self):
        with pytest.raises(TypeError):
            BaseChannel(config=MagicMock(), bus=MagicMock())

    def test_channel_implementation(self):

        class TestChannel(BaseChannel):
            name = "test"

            async def start(self):
                self._running = True

            async def stop(self):
                self._running = False

            async def send(self, message, **kwargs):
                return True

        config = SlackConfig()
        channel = TestChannel(config, MagicMock())
        assert channel.config == config
        assert channel.is_running is False

    @pytest.mark.asyncio
    async def test_channel_start(self):

        class TestChannel(BaseChannel):
            name = "test"

            async def start(self):
                self._running = True

            async def stop(self):
                self._running = False

            async def send(self, message, **kwargs):
                return True

        config = SlackConfig()
        channel = TestChannel(config, MagicMock())
        await channel.start()
        assert channel.is_running is True

    @pytest.mark.asyncio
    async def test_channel_send(self):

        class TestChannel(BaseChannel):
            name = "test"

            async def start(self):
                pass

            async def stop(self):
                pass

            async def send(self, message, **kwargs):
                return True

        config = SlackConfig()
        channel = TestChannel(config, MagicMock())
        msg = OutboundMessage(content="Test", channel="test", chat_id="123")
        result = await channel.send(msg)
        assert result is True

    @pytest.mark.asyncio
    async def test_channel_send_failure(self):

        class TestChannel(BaseChannel):
            name = "test"

            async def start(self):
                pass

            async def stop(self):
                pass

            async def send(self, message, **kwargs):
                raise Exception("Send failed")

        config = SlackConfig()
        channel = TestChannel(config, MagicMock())
        msg = OutboundMessage(content="Test", channel="test", chat_id="123")
        with pytest.raises(Exception, match="Send failed"):
            await channel.send(msg)
