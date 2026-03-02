import asyncio
import email
import imaplib
import json
import re
import smtplib

import websockets

from nanobot.bus.events import InboundMessage


def _slackify(text):
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", text)
    return text.replace("**", "*")


class BaseChannel:
    def __init__(self, config, bus):
        self.config, self.bus = config, bus

    async def _handle(self, sid, cid, content, meta=None):
        await self.bus.publish_inbound(
            InboundMessage(
                channel=self.name,
                sender_id=sid,
                chat_id=cid,
                content=content or "",
                metadata=meta or {},
            )
        )


class WhatsappChannel(BaseChannel):
    name = "whatsapp"

    async def start(self):
        me = None
        while True:
            try:
                async with websockets.connect(self.config.bridge_url) as ws:
                    self.ws = ws
                    if self.config.bridge_token:
                        await ws.send(
                            json.dumps({"type": "auth", "token": self.config.bridge_token})
                        )
                    async for m in ws:
                        d = json.loads(m)
                        if d.get("type") == "status" and d.get("status", "").startswith("me:"):
                            me = d["status"][3:]
                        if d.get("type") == "message":
                            sid, sender = d.get("sender", ""), d.get("sender", "")
                            if d.get("fromMe") and (not me or sid == me):
                                sid = "me"
                            elif not self.config.allow_from and not d.get("fromMe"):
                                continue
                            await self._handle(sid, sender, d.get("content", ""))
            except Exception:
                self.ws = None
                await asyncio.sleep(5)

    async def send(self, msg):
        if getattr(self, "ws", None):
            await self.ws.send(json.dumps({"type": "send", "to": msg.chat_id, "text": msg.content}))


class TelegramChannel(BaseChannel):
    name = "telegram"

    async def start(self):
        from telegram.ext import Application, MessageHandler, filters

        app = Application.builder().token(self.config.token).build()

        async def h(u, c):
            await self._handle(str(u.effective_user.id), str(u.effective_chat.id), u.message.text)

        app.add_handler(MessageHandler(filters.TEXT, h))
        await app.initialize()
        await app.start_polling()

    async def send(self, msg):
        from telegram import Bot

        async with Bot(self.config.token) as b:
            for c in [msg.content[i : i + 4000] for i in range(0, len(msg.content), 4000)]:
                await b.send_message(msg.chat_id, c or "...")


class DiscordChannel(BaseChannel):
    name = "discord"

    async def start(self):
        import discord

        self.client = discord.Client(intents=discord.Intents.default())

        @self.client.event
        async def on_message(m):
            if not m.author.bot:
                tid = str(m.id) if hasattr(m.channel, "threads") else None
                await self._handle(
                    str(m.author.id), str(m.channel.id), m.content, meta={"thread_id": tid}
                )

        await self.client.start(self.config.token)

    async def send(self, msg):
        if not getattr(self, "client", None) or self.client.is_closed():
            return
        ch = await self.client.fetch_channel(int(msg.chat_id))
        for c in [msg.content[i : i + 2000] for i in range(0, len(msg.content), 2000)]:
            await ch.send(c or "...")


class SlackChannel(BaseChannel):
    name = "slack"

    async def start(self):
        from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
        from slack_bolt.app.async_app import AsyncApp

        app = AsyncApp(token=self.config.bot_token)

        @app.event("message")
        async def h(e, s):
            tid = e.get("thread_ts")
            await self.bus.publish_inbound(
                InboundMessage(
                    self.name,
                    e["user"],
                    e["channel"],
                    e.get("text", ""),
                    session_key_override=f"slack:{e['channel']}:{tid}" if tid else None,
                    metadata={"thread_ts": tid},
                )
            )

        await AsyncSocketModeHandler(app, self.config.app_token).start_async()

    async def send(self, msg):
        from slack_sdk.web.async_client import AsyncWebClient

        await AsyncWebClient(token=self.config.bot_token).chat_postMessage(
            channel=msg.chat_id,
            text=_slackify(msg.content),
            thread_ts=msg.metadata.get("thread_ts"),
        )


class EmailChannel(BaseChannel):
    name = "email"

    async def start(self):
        while True:
            try:
                mail = imaplib.IMAP4_SSL(self.config.imap_host, self.config.imap_port)
                mail.login(self.config.imap_username, self.config.imap_password)
                mail.select("inbox")
                _, data = mail.search(None, "UNSEEN")
                for n in data[0].split():
                    _, d = mail.fetch(n, "(RFC822)")
                    raw = email.message_from_bytes(d[0][1])
                    body = ""
                    if raw.is_multipart():
                        for p in raw.walk():
                            if p.get_content_type() == "text/plain":
                                body = p.get_payload(decode=True).decode()
                                break
                    else:
                        body = raw.get_payload(decode=True).decode()
                    s = email.utils.parseaddr(raw["From"])[1]
                    await self._handle(s, s, body)
                mail.logout()
            except Exception:
                pass
            await asyncio.sleep(self.config.poll_interval_seconds)

    async def send(self, msg):
        from email.message import EmailMessage

        m = EmailMessage()
        m.set_content(msg.content)
        m["Subject"] = "AI Response"
        m["To"] = msg.chat_id
        m["From"] = self.config.from_address
        with smtplib.SMTP_SSL(self.config.smtp_host, self.config.smtp_port) as s:
            s.login(self.config.smtp_username, self.config.smtp_password)
            s.send_message(m)
