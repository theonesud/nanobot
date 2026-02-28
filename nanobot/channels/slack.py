"""Slack channel implementation using Bolt for Python."""

import asyncio
import re
from typing import Any

from loguru import logger
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.app.async_app import AsyncApp
from slackify_markdown import slackify_markdown

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import SlackConfig


class SlackChannel(BaseChannel):
    """Slack channel using Bolt and Socket Mode."""

    name = "slack"

    def __init__(self, config: SlackConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: SlackConfig = config
        self._app: AsyncApp | None = None
        self._handler: AsyncSocketModeHandler | None = None
        self._bot_user_id: str | None = None

    async def start(self) -> None:
        """Start the Slack Bolt app in Socket Mode."""
        if not self.config.bot_token or not self.config.app_token:
            logger.error("Slack bot/app token not configured")
            return

        self._running = True
        self._app = AsyncApp(token=self.config.bot_token)

        # Register listeners
        self._app.event("message")(self._on_bolt_event)
        self._app.event("app_mention")(self._on_bolt_event)

        # Action handlers for interactive elements (e.g., security prompts)
        self._app.action(re.compile("^(approve|reject)_.*"))(self._handle_action)

        self._handler = AsyncSocketModeHandler(self._app, self.config.app_token)

        # Resolve bot user ID
        try:
            auth = await self._app.client.auth_test()
            self._bot_user_id = auth.get("user_id")
            logger.info("Slack bot connected as {}", self._bot_user_id)
        except Exception as e:
            logger.warning("Slack auth_test failed: {}", e)

        logger.info("Starting Slack Socket Mode client via Bolt...")
        asyncio.create_task(self._listen_for_approvals())
        await self._handler.start_async()

    async def _on_bolt_event(self, event: dict[str, Any], say: Any) -> None:
        """Handle incoming Slack messages/mentions."""
        logger.debug("Slack event received: {}", event.get("type"))

        channel_id = event.get("channel")
        user_id = event.get("user")
        text = event.get("text", "")
        thread_ts = event.get("thread_ts") or event.get("ts")
        channel_type = event.get("channel_type")

        if not channel_id or not user_id:
            return

        if user_id == self._bot_user_id:
            return

        if not self._is_allowed(user_id, channel_id, channel_type):
            return

        if not self._should_respond_in_channel(event.get("type"), text, channel_id):
            return

        # God Mode detection
        is_godmode = False
        if text.startswith("/godmode"):
            is_godmode = True
            text = text.replace("/godmode", "", 1).strip()
            logger.info("🚨 GOD MODE triggered by <@{}>", user_id)

        msg_text = self._strip_bot_mention(text)

        metadata = {
            "slack_event_type": event.get("type"),
            "thread_ts": thread_ts,
            "is_godmode": is_godmode,
        }

        await self._handle_message(
            sender_id=user_id,
            chat_id=channel_id,
            content=msg_text,
            metadata=metadata,
            session_key=f"slack:{channel_id}:{thread_ts}" if thread_ts else None,
        )

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message to Slack with mrkdwn conversion."""
        if not self._app:
            return

        thread_ts = msg.metadata.get("thread_ts")
        mrkdwn_content = self._to_mrkdwn(msg.content)

        try:
            await self._app.client.chat_postMessage(
                channel=msg.chat_id,
                text=mrkdwn_content,
                thread_ts=thread_ts,
                # Prefer blocks if there are multiple sections or images, but for now simple text
            )
        except Exception as e:
            logger.error("Failed to send Slack message: {}", e)

    async def _listen_for_approvals(self) -> None:
        """Background task to listen for approval requests on the bus."""
        while self._running:
            try:
                req = await self.bus.consume_approval_request()
                if req.channel != "slack":
                    continue

                thread_ts = req.metadata.get("thread_ts")

                blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"🛡️ *Security Audit Required*\nNanobot wants to execute a command flagged as potentially unsafe:\n`{req.command}`",
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
                ]

                await self._app.client.chat_postMessage(
                    channel=req.chat_id,
                    thread_ts=thread_ts,
                    blocks=blocks,
                    text=f"Security Audit: {req.command}",
                )
            except Exception as e:
                logger.error("Error in Slack approval listener: {}", e)
                await asyncio.sleep(1)

    async def _handle_action(self, ack: Any, body: dict[str, Any], action: dict[str, Any]) -> None:
        """Handle interactive actions (buttons)."""
        await ack()
        action_id = action.get("action_id", "")
        logger.info("Slack action received: {}", action_id)

        from nanobot.bus.events import ApprovalResponse

        approved = action_id.startswith("approve_")
        request_id = action_id.replace("approve_", "").replace("reject_", "")

        # Update original message to remove buttons
        try:
            channel_id = body.get("channel", {}).get("id")
            message_ts = body.get("message", {}).get("ts")
            user_id = body.get("user", {}).get("id")

            status_text = "✅ Approved" if approved else "❌ Rejected"

            await self._app.client.chat_update(
                channel=channel_id,
                ts=message_ts,
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"🛡️ *Security Audit:* {status_text} by <@{user_id}>\n`{body.get('message', {}).get('blocks', [{}])[0].get('text', {}).get('text', '').split('`')[1]}`",
                        },
                    }
                ],
                text=f"Security Audit: {status_text}",
            )

            # Publish response to bus
            await self.bus.publish_approval_response(
                ApprovalResponse(id=request_id, approved=approved, responder_id=user_id)
            )

        except Exception as e:
            logger.error("Failed to update Slack message after action: {}", e)

    def _is_allowed(self, sender_id: str, chat_id: str, channel_type: str) -> bool:
        if channel_type == "im":
            if not self.config.dm.enabled:
                return False
            if self.config.dm.policy == "allowlist":
                return sender_id in self.config.dm.allow_from
            return True

        if self.config.group_policy == "allowlist":
            return chat_id in self.config.group_allow_from
        return True

    def _should_respond_in_channel(self, event_type: str, text: str, chat_id: str) -> bool:
        if self.config.group_policy == "open":
            return True
        if self.config.group_policy == "mention":
            if event_type == "app_mention":
                return True
            return self._bot_user_id is not None and f"<@{self._bot_user_id}>" in text
        if self.config.group_policy == "allowlist":
            return chat_id in self.config.group_allow_from
        return False

    def _strip_bot_mention(self, text: str) -> str:
        if not text or not self._bot_user_id:
            return text
        return re.sub(rf"<@{re.escape(self._bot_user_id)}>\s*", "", text).strip()

    _TABLE_RE = re.compile(r"(?m)^\|.*\|$(?:\n\|[\s:|-]*\|$)(?:\n\|.*\|$)*")
    _CODE_FENCE_RE = re.compile(r"```[\s\S]*?```")
    _INLINE_CODE_RE = re.compile(r"`[^`]+`")
    _LEFTOVER_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
    _LEFTOVER_HEADER_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
    _BARE_URL_RE = re.compile(r"(?<![|<])(https?://\S+)")

    @classmethod
    def _to_mrkdwn(cls, text: str) -> str:
        """Convert Markdown to Slack mrkdwn, including tables."""
        if not text:
            return ""
        text = cls._TABLE_RE.sub(cls._convert_table, text)
        return cls._fixup_mrkdwn(slackify_markdown(text))

    @classmethod
    def _fixup_mrkdwn(cls, text: str) -> str:
        """Fix markdown artifacts that slackify_markdown misses."""
        code_blocks: list[str] = []

        def _save_code(m: re.Match) -> str:
            code_blocks.append(m.group(0))
            return f"\x00CB{len(code_blocks) - 1}\x00"

        text = cls._CODE_FENCE_RE.sub(_save_code, text)
        text = cls._INLINE_CODE_RE.sub(_save_code, text)
        text = cls._LEFTOVER_BOLD_RE.sub(r"*\1*", text)
        text = cls._LEFTOVER_HEADER_RE.sub(r"*\1*", text)
        text = cls._BARE_URL_RE.sub(lambda m: m.group(0).replace("&amp;", "&"), text)

        for i, block in enumerate(code_blocks):
            text = text.replace(f"\x00CB{i}\x00", block)
        return text

    @staticmethod
    def _convert_table(match: re.Match) -> str:
        """Convert a Markdown table to a Slack-readable list."""
        lines = [ln.strip() for ln in match.group(0).strip().splitlines() if ln.strip()]
        if len(lines) < 2:
            return match.group(0)
        headers = [h.strip() for h in lines[0].strip("|").split("|")]
        start = 2 if re.fullmatch(r"[|\s:\-]+", lines[1]) else 1
        rows: list[str] = []
        for line in lines[start:]:
            cells = [c.strip() for c in line.strip("|").split("|")]
            cells = (cells + [""] * len(headers))[: len(headers)]
            parts = [f"**{headers[i]}**: {cells[i]}" for i in range(len(headers)) if cells[i]]
            if parts:
                rows.append(" · ".join(parts))
        return "\n".join(rows)
