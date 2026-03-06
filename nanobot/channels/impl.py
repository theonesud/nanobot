import asyncio
import json
import re
import smtplib

import websockets
from loguru import logger

from nanobot.bus.events import InboundMessage


def _slackify(text):
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", text)
    return text.replace("**", "*")


class BaseChannel:
    def __init__(self, config, bus, provider=None):
        self.config, self.bus, self.provider = config, bus, provider

    async def _handle(self, sid, cid, content, meta=None, media=None, session_key_override=None):
        meta = meta or {}
        meta["sender_id"] = sid
        await self.bus.publish_inbound(
            InboundMessage(
                channel=self.name,
                chat_id=cid,
                content=content or "",
                metadata=meta,
                media=media or [],
                session_key_override=session_key_override,
            )
        )

    async def approve(self, req):
        pass


class WhatsappChannel(BaseChannel):
    name = "whatsapp"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ws = None
        self._ws_lock = asyncio.Lock()

    async def start(self):
        me, authed = None, not self.config.bridge_token
        while True:
            try:
                async with websockets.connect(self.config.bridge_url) as ws:
                    async with self._ws_lock:
                        self.ws = ws
                    if self.config.bridge_token:
                        await ws.send(
                            json.dumps({"type": "auth", "token": self.config.bridge_token})
                        )
                    async for m in ws:
                        d = json.loads(m)
                        if d.get("type") == "status":
                            st = d.get("status", "")
                            if st.startswith("me:"):
                                me = st[3:]
                            if st == "connected" or st.startswith("me:"):
                                authed = True
                        if d.get("type") == "message" and authed:
                            sid, sender = d.get("sender", ""), d.get("sender", "")
                            if d.get("fromMe") and (not me or sid == me):
                                sid = "me"
                            elif not d.get("fromMe"):
                                if self.config.allow_from and sid not in self.config.allow_from:
                                    continue
                            await self._handle(
                                sid, sender, d.get("content", ""), media=d.get("media", [])
                            )
            except Exception as e:
                logger.error(f"WhatsApp error: {e}")
                async with self._ws_lock:
                    self.ws = None
                authed = not self.config.bridge_token
                await asyncio.sleep(5)

    async def send(self, msg):
        async with self._ws_lock:
            if self.ws:
                try:
                    await self.ws.send(
                        json.dumps({"type": "send", "to": msg.chat_id, "text": msg.content})
                    )
                except Exception:
                    logger.debug("WhatsApp send failed", exc_info=True)


class TelegramChannel(BaseChannel):
    name = "telegram"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._app = None
        self._stop_event = asyncio.Event()

    async def start(self):
        from telegram.ext import Application, MessageHandler, filters

        self._app = Application.builder().token(self.config.token).build()

        async def h(u, c):
            m, imgs = u.message, []
            if m.photo:
                f = await m.photo[-1].get_file()
                imgs.append(f.file_path)
            await self._handle(
                str(u.effective_user.id),
                str(u.effective_chat.id),
                m.text or m.caption or "",
                media=imgs,
            )

        self._app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, h))
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        try:
            await self._stop_event.wait()
        finally:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    async def send(self, msg):
        from telegram import Bot

        async with Bot(self.config.token) as b:
            for c in [msg.content[i : i + 4000] for i in range(0, len(msg.content), 4000)]:
                await b.send_message(msg.chat_id, c or "...")

    def stop(self):
        self._stop_event.set()


class DiscordChannel(BaseChannel):
    name = "discord"

    async def start(self):
        import discord

        intents = discord.Intents.default()
        intents.message_content = True
        self.client = discord.Client(intents=intents)

        @self.client.event
        async def on_message(m):
            if not m.author.bot:
                tid = str(m.channel.id) if isinstance(m.channel, discord.Thread) else None
                imgs = [
                    a.url for a in m.attachments if a.content_type and "image" in a.content_type
                ]
                await self._handle(
                    str(m.author.id),
                    str(m.channel.id),
                    m.content,
                    meta={"thread_id": tid},
                    media=imgs,
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
            imgs = []
            for f in e.get("files", []):
                if f.get("mimetype", "").startswith("image/"):
                    imgs.append(f.get("url_private_download") or f.get("url_private"))
            sk = f"slack:{e['channel']}:{tid}" if tid else None
            await self._handle(
                e.get("user", "unknown"),
                e["channel"],
                e.get("text", ""),
                meta={"thread_ts": tid},
                media=imgs,
                session_key_override=sk,
            )

        @app.action(re.compile("(approve|reject)_.*"))
        async def handle_approval(ack, body):
            await ack()
            action_id = body["actions"][0]["action_id"]
            rid = action_id.split("_", 1)[1]
            approved = action_id.startswith("approve_")
            from nanobot.bus.events import ApprovalResponse

            await self.bus.publish_approval_response(ApprovalResponse(id=rid, approved=approved))
            await app.client.chat_update(
                channel=body["channel"]["id"],
                ts=body["message"]["ts"],
                text="✅ Approved" if approved else "❌ Rejected",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"Decision: *{'Approved' if approved else 'Rejected'}*",
                        },
                    }
                ],
            )

        handler = AsyncSocketModeHandler(app, self.config.app_token)
        self.app = app
        await handler.start_async()

    async def approve(self, req):
        if not getattr(self, "app", None):
            return
        await self.app.client.chat_postMessage(
            channel=req.chat_id,
            text=f"Approval Required: {req.title}",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Approval Required: {req.title}*\n`{req.content}`",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Approve"},
                            "style": "primary",
                            "action_id": f"approve_{req.id}",
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Reject"},
                            "style": "danger",
                            "action_id": f"reject_{req.id}",
                        },
                    ],
                },
            ],
        )

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

                def _check_mail():
                    import email as email_mod
                    import imaplib

                    m = imaplib.IMAP4_SSL(self.config.imap_host, self.config.imap_port)
                    try:
                        m.login(self.config.imap_username, self.config.imap_password)
                        m.select("inbox")
                        _, data = m.search(None, "UNSEEN")
                        res = []
                        for n in data[0].split():
                            _, d = m.fetch(n, "(RFC822)")
                            raw = email_mod.message_from_bytes(d[0][1])
                            body = ""
                            if raw.is_multipart():
                                for p in raw.walk():
                                    if p.get_content_type() == "text/plain":
                                        charset = p.get_content_charset() or "utf-8"
                                        payload = p.get_payload(decode=True)
                                        if payload:
                                            body = payload.decode(charset, errors="replace")
                                        break
                            else:
                                charset = raw.get_content_charset() or "utf-8"
                                payload = raw.get_payload(decode=True)
                                if payload:
                                    body = payload.decode(charset, errors="replace")
                            res.append(
                                (
                                    email_mod.utils.parseaddr(raw["From"])[1],
                                    raw["Subject"] or "No Subject",
                                    body,
                                )
                            )
                    finally:
                        try:
                            m.logout()
                        except Exception:
                            pass
                    return res

                msgs = await asyncio.to_thread(_check_mail)
                for s, subject, body in msgs:
                    if self.provider:
                        try:
                            t_resp = await self.provider.chat(
                                [
                                    {
                                        "role": "system",
                                        "content": "Triage. Respond YES/NO: is this urgent?",
                                    },
                                    {
                                        "role": "user",
                                        "content": f"Subject: {subject}\nFrom: {s}\nBody: {body[:500]}",
                                    },
                                ]
                            )
                            if "YES" not in (t_resp.content or "").upper():
                                logger.info(f"📧 Email from {s} triaged as non-urgent.")
                                continue
                        except Exception:
                            logger.warning("Email triage failed, processing anyway", exc_info=True)

                    await self._handle(s, s, body, meta={"subject": subject})
            except Exception as e:
                logger.error(f"Email error: {e}")
            await asyncio.sleep(self.config.poll_interval_seconds)

    async def send(self, msg):
        from email.message import EmailMessage

        def _send():
            m = EmailMessage()
            m.set_content(msg.content)
            m["Subject"] = msg.metadata.get("subject", "AI Response")
            m["To"] = msg.chat_id
            m["From"] = self.config.from_address
            if self.config.smtp_port == 465:
                with smtplib.SMTP_SSL(self.config.smtp_host, self.config.smtp_port) as s:
                    s.login(self.config.smtp_username, self.config.smtp_password)
                    s.send_message(m)
            else:
                with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port) as s:
                    s.starttls()
                    s.login(self.config.smtp_username, self.config.smtp_password)
                    s.send_message(m)

        await asyncio.to_thread(_send)
