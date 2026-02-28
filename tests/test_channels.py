"""Tests for channels (base classes and implementations)."""

import pytest
from abc import ABC
from unittest.mock import AsyncMock, MagicMock

from nanobot.channels.base import BaseChannel
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.config.schema import SlackConfig


class TestChannelConfig:
    """Tests for ChannelConfig."""

    def test_config_creation(self):
        """Test creating a channel config."""
        config = SlackConfig(enabled=True)
        assert config.enabled == True


class TestMessage:
    """Tests for Message."""

    def test_message_creation(self):
        """Test creating a message."""
        msg = InboundMessage(content="Hello", sender_id="user123", channel="slack", chat_id="C123")
        assert msg.content == "Hello"
        assert msg.sender_id == "user123"
        assert msg.channel == "slack"

    def test_outbound_message(self):
        """Test outbound message."""
        msg = OutboundMessage(
            content="Hello",
            channel="slack",
            chat_id="C123",
        )
        assert msg.chat_id == "C123"


class TestChannel:
    """Tests for base Channel class."""

    def test_channel_abstract(self):
        """Test that Channel is abstract."""
        with pytest.raises(TypeError):
            BaseChannel(config=MagicMock(), bus=MagicMock())

    def test_channel_implementation(self):
        """Test implementing a channel."""

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
        """Test channel start."""

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
        """Test channel send."""

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
        """Test channel send failure."""

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
        with pytest.raises(Exception):
            await channel.send(msg)
