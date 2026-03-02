import asyncio

from loguru import logger

from nanobot.channels import impl


class ChannelManager:
    def __init__(self, config, bus, provider=None):
        self.config, self.bus, self.channels = config, bus, {}
        for n in ["whatsapp", "telegram", "discord", "email", "slack"]:
            c = getattr(self.config.channels, n, None)
            if c and c.enabled:
                cls = getattr(impl, f"{n.title()}Channel")
                self.channels[n] = cls(c, self.bus)
                logger.debug(f"{n} enabled")

    async def start_all(self):
        asyncio.create_task(self._outbound())
        await asyncio.gather(*[c.start() for c in self.channels.values()], return_exceptions=True)

    async def _outbound(self):
        while True:
            try:
                m = await self.bus.consume_outbound()
                if m.channel in self.channels:
                    await self.channels[m.channel].send(m)
            except Exception:
                await asyncio.sleep(1)

    async def stop_all(self):
        pass
