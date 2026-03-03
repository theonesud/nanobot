import asyncio
import json

from loguru import logger

from nanobot.channels import impl


class ChannelManager:
    def __init__(self, config, bus, provider=None):
        self.config, self.bus, self.channels = config, bus, {}
        for n in ["whatsapp", "telegram", "discord", "email", "slack"]:
            c = getattr(self.config.channels, n, None)
            if c and c.enabled:
                cls = getattr(impl, f"{n.title()}Channel")
                self.channels[n] = cls(c, self.bus, provider)
                logger.debug(f"{n} enabled")

    async def start_all(self):
        asyncio.create_task(self._outbound())
        asyncio.create_task(self._webhook())
        asyncio.create_task(self._approvals())
        await asyncio.gather(*[c.start() for c in self.channels.values()], return_exceptions=True)

    async def _approvals(self):
        while True:
            try:
                r = await self.bus.consume_approval_request()
                if r.channel in self.channels:
                    await self.channels[r.channel].approve(r)
            except Exception:
                await asyncio.sleep(1)

    async def _webhook(self):
        async def h(r, w):
            d = await r.read(4000)
            if b"POST" in d:
                try:
                    b = d.split(b"\r\n\r\n")[1]
                    j = json.loads(b)
                    from nanobot.bus.events import InboundMessage

                    await self.bus.publish_inbound(
                        InboundMessage(
                            j.get("channel", "webhook"),
                            j.get("chat_id", "external"),
                            j.get("content", ""),
                            metadata=j.get("metadata", {}),
                        )
                    )
                    w.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
                except Exception:
                    w.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            w.close()

        s = await asyncio.start_server(h, "0.0.0.0", 8080)
        async with s:
            await s.serve_forever()

    async def _outbound(self):
        while True:
            try:
                m = await self.bus.consume_outbound()
                if m.channel in self.channels:
                    await self.channels[m.channel].send(m)
            except Exception:
                await asyncio.sleep(1)
