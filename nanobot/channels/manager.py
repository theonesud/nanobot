from __future__ import annotations

import asyncio
import importlib

from loguru import logger

from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import Config
from nanobot.providers.base import LLMProvider


class ChannelManager:
    def __init__(self, config: Config, bus: MessageBus, provider: LLMProvider | None = None):
        self.config = config
        self.bus = bus
        self.provider = provider
        self.channels: dict[str, BaseChannel] = {}
        self._dispatch_task: asyncio.Task | None = None
        self._init_channels()

    def _init_channels(self) -> None:
        for name, cls_name, kwargs in [
            ("telegram", "TelegramChannel", {"groq_api_key": self.config.providers.groq.api_key}),
            ("whatsapp", "WhatsAppChannel", {}),
            ("discord", "DiscordChannel", {}),
            ("feishu", "FeishuChannel", {}),
            ("mochat", "MochatChannel", {}),
            ("dingtalk", "DingTalkChannel", {}),
            ("email", "EmailChannel", {"provider": self.provider}),
            ("slack", "SlackChannel", {}),
            ("qq", "QQChannel", {}),
            ("matrix", "MatrixChannel", {}),
        ]:
            cfg = getattr(self.config.channels, name, None)
            if cfg and cfg.enabled:
                try:
                    module = importlib.import_module(f"nanobot.channels.{name}")
                    self.channels[name] = getattr(module, cls_name)(cfg, self.bus, **kwargs)
                    logger.info("{} channel enabled", name.title())
                except ImportError as e:
                    logger.warning("{} channel not available: {}", name.title(), e)

    async def start_all(self) -> None:
        if not self.channels:
            logger.warning("No channels enabled")
            return
        self._dispatch_task = asyncio.create_task(self._dispatch_outbound())

        async def _safe_start(n, c):
            try:
                await c.start()
            except Exception as e:
                logger.error("Failed to start channel {}: {}", n, e)

        await asyncio.gather(
            *[asyncio.create_task(_safe_start(n, c)) for n, c in self.channels.items()],
            return_exceptions=True,
        )

    async def stop_all(self) -> None:
        logger.info("Stopping all channels...")
        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
        for name, channel in self.channels.items():
            try:
                await channel.stop()
                logger.info("Stopped {} channel", name)
            except Exception as e:
                logger.error("Error stopping {}: {}", name, e)

    async def _dispatch_outbound(self) -> None:
        logger.info("Outbound dispatcher started")
        while True:
            try:
                msg = await asyncio.wait_for(self.bus.consume_outbound(), timeout=1.0)
                if msg.metadata.get("_progress"):
                    if msg.metadata.get("_tool_hint") and (
                        not self.config.channels.send_tool_hints
                    ):
                        continue
                    if not msg.metadata.get("_tool_hint") and (
                        not self.config.channels.send_progress
                    ):
                        continue
                channel = self.channels.get(msg.channel)
                if channel:
                    try:
                        await channel.send(msg)
                    except Exception as e:
                        logger.error("Error sending to {}: {}", msg.channel, e)
                else:
                    logger.warning("Unknown channel: {}", msg.channel)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Critical error in outbound dispatcher: {}", e)
                await asyncio.sleep(5)

    @property
    def enabled_channels(self) -> list[str]:
        return list(self.channels.keys())
