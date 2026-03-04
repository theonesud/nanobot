from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class InboundMessage:
    channel: str
    chat_id: str
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    priority: int = 50
    session_key_override: str | None = None

    @property
    def session_key(self) -> str:
        return self.session_key_override or f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    channel: str
    chat_id: str
    content: str
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
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
