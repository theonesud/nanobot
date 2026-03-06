import asyncio
import json

from loguru import logger

from nanobot.channels import impl


class ChannelManager:
    def __init__(self, config, bus, provider=None):
        self.config, self.bus, self.channels = config, bus, {}
        self._tasks: list[asyncio.Task] = []
        for n in ["whatsapp", "telegram", "discord", "email", "slack"]:
            c = getattr(self.config.channels, n, None)
            if c and c.enabled:
                cls = getattr(impl, f"{n.title()}Channel")
                self.channels[n] = cls(c, self.bus, provider)
                logger.debug(f"{n} enabled")

    async def start_all(self):
        self._tasks = [
            asyncio.create_task(self._outbound(), name="outbound"),
            asyncio.create_task(self._webhook(), name="webhook"),
            asyncio.create_task(self._approvals(), name="approvals"),
        ]
        for t in self._tasks:
            t.add_done_callback(self._task_done)

        results = await asyncio.gather(
            *[c.start() for c in self.channels.values()], return_exceptions=True
        )
        for name, result in zip(self.channels.keys(), results, strict=False):
            if isinstance(result, Exception):
                logger.error("Channel {} failed to start: {}", name, result)

    @staticmethod
    def _task_done(task: asyncio.Task):
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error("Background task '{}' died: {}", task.get_name(), exc)

    async def stop_all(self):
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _approvals(self):
        while True:
            try:
                r = await self.bus.consume_approval_request()
                if r.channel in self.channels:
                    await self.channels[r.channel].approve(r)
            except Exception:
                logger.exception("Approval handler error")
                await asyncio.sleep(1)

    async def _webhook(self):
        webhook_cfg = getattr(self.config, "webhook", None)
        host = getattr(webhook_cfg, "host", "127.0.0.1") if webhook_cfg else "127.0.0.1"
        port = getattr(webhook_cfg, "port", 8080) if webhook_cfg else 8080
        token = getattr(webhook_cfg, "token", "") if webhook_cfg else ""

        async def h(r, w):
            try:
                d = await r.read(65536)
                if b"POST" not in d[:16]:
                    w.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
                    await w.drain()
                    w.close()
                    await w.wait_closed()
                    return

                parts = d.split(b"\r\n\r\n", 1)
                if len(parts) < 2:
                    w.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                    await w.drain()
                    w.close()
                    await w.wait_closed()
                    return

                body = parts[1]
                j = json.loads(body)

                if token and j.get("token") != token:
                    w.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                    await w.drain()
                    w.close()
                    await w.wait_closed()
                    return

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
            except json.JSONDecodeError:
                w.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            except Exception:
                logger.exception("Webhook handler error")
                w.write(b"HTTP/1.1 500 Internal Server Error\r\n\r\n")
            finally:
                try:
                    await w.drain()
                    w.close()
                    await w.wait_closed()
                except Exception:
                    pass

        s = await asyncio.start_server(h, host, port)
        logger.info("Webhook server listening on {}:{}", host, port)
        async with s:
            await s.serve_forever()

    async def _outbound(self):
        while True:
            try:
                m = await self.bus.consume_outbound()
                if m.channel in self.channels:
                    await self.channels[m.channel].send(m)
            except Exception:
                logger.exception("Outbound handler error")
                await asyncio.sleep(1)
