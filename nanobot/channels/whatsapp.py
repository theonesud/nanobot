import asyncio
import json

import websockets
from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import WhatsAppConfig


class WhatsAppChannel(BaseChannel):
    name = "whatsapp"

    def __init__(self, config: WhatsAppConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: WhatsAppConfig = config
        self._ws = None
        self._connected = False
        self._me_jid = None

    async def start(self) -> None:
        bridge_url = self.config.bridge_url
        logger.info("Connecting to WhatsApp bridge at {}...", bridge_url)
        self._running = True
        while self._running:
            try:
                async with websockets.connect(bridge_url) as ws:
                    self._ws = ws
                    if self.config.bridge_token:
                        await ws.send(
                            json.dumps({"type": "auth", "token": self.config.bridge_token})
                        )
                    self._connected = True
                    logger.info("Connected to WhatsApp bridge")
                    async for message in ws:
                        try:
                            await self._handle_bridge_message(message)
                        except Exception as e:
                            logger.error("Error handling bridge message: {}", e)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._connected = False
                self._ws = None
                logger.warning("WhatsApp bridge connection error: {}", e)
                if self._running:
                    logger.info("Reconnecting in 5 seconds...")
                    await asyncio.sleep(5)

    async def stop(self) -> None:
        self._running = False
        self._connected = False
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def send(self, msg: OutboundMessage) -> None:
        if not self._ws or not self._connected:
            logger.warning("WhatsApp bridge not connected")
            return
        try:
            content = msg.content
            if msg.metadata.get("_progress"):
                content = f"_{content}_"
            payload = {"type": "send", "to": msg.chat_id, "text": content}
            await self._ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            logger.error("Error sending WhatsApp message: {}", e)

    async def _handle_bridge_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from bridge: {}", raw[:100])
            return
        msg_type = data.get("type")
        if msg_type == "message":
            from_me = data.get("fromMe", False)
            sender = data.get("sender", "")

            if from_me:
                if self._me_jid and sender != self._me_jid:
                    return
                sender_id = self._me_jid.split("@")[0] if self._me_jid else "me"
            elif not self.config.allow_from:
                return
            else:
                pn = data.get("pn", "")
                user_id = pn if pn else sender
                sender_id = user_id.split("@")[0] if "@" in user_id else user_id

            content = data.get("content", "")
            if content == "[Voice Message]":
                content = "[Voice Message: Transcription not available for WhatsApp yet]"

            await self._handle_message(
                sender_id=sender_id,
                chat_id=sender,
                content=content,
                metadata={
                    "message_id": data.get("id"),
                    "timestamp": data.get("timestamp"),
                    "is_group": data.get("isGroup", False),
                    "from_me": from_me,
                },
            )
        elif msg_type == "status":
            status = data.get("status", "")
            if status.startswith("me:"):
                self._me_jid = status[3:]
                logger.info("WhatsApp Owner JID: {}", self._me_jid)
            else:
                logger.info("WhatsApp status: {}", status)
                if status == "connected":
                    self._connected = True
                elif status == "disconnected":
                    self._connected = False
        elif msg_type == "qr":
            logger.info("Scan QR code in the bridge terminal to connect WhatsApp")
        elif msg_type == "error":
            logger.error("WhatsApp bridge error: {}", data.get("error"))
