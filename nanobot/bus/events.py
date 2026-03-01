from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class InboundMessage:
    channel: str
    sender_id: str
    chat_id: str
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    session_key_override: str | None = None

    @property
    def session_key(self) -> str:
        return self.session_key_override or f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    channel: str
    chat_id: str
    content: str
    reply_to: str | None = None
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApprovalRequest:
    id: str
    channel: str
    chat_id: str
    type: str = "shell"
    title: str = ""
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApprovalResponse:
    id: str
    approved: bool
    reason: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
