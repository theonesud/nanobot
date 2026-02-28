"""Async message queue for decoupled channel-agent communication."""

import asyncio

from nanobot.bus.events import ApprovalRequest, ApprovalResponse, InboundMessage, OutboundMessage


class MessageBus:
    """
    Async message bus that decouples chat channels from the agent core.

    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.
    """

    def __init__(self):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        self.approval_requests: asyncio.Queue[ApprovalRequest] = asyncio.Queue()
        self.approval_responses: dict[str, asyncio.Queue[ApprovalResponse]] = {}

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent."""
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels."""
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available)."""
        return await self.outbound.get()

    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages."""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages."""
        return self.outbound.qsize()

    async def publish_approval_request(self, req: ApprovalRequest) -> None:
        """Publish an approval request to the UI/Channel layer."""
        if req.id not in self.approval_responses:
            self.approval_responses[req.id] = asyncio.Queue()
        await self.approval_requests.put(req)

    async def consume_approval_request(self) -> ApprovalRequest:
        """Consume the next approval request."""
        return await self.approval_requests.get()

    async def publish_approval_response(self, resp: ApprovalResponse) -> None:
        """Publish a response to an approval request."""
        if resp.id in self.approval_responses:
            await self.approval_responses[resp.id].put(resp)

    async def wait_for_approval(
        self, request_id: str, timeout: float = 300.0
    ) -> ApprovalResponse | None:
        """Wait for a response to a specific approval request."""
        if request_id not in self.approval_responses:
            self.approval_responses[request_id] = asyncio.Queue()

        try:
            return await asyncio.wait_for(
                self.approval_responses[request_id].get(), timeout=timeout
            )
        except asyncio.TimeoutError:
            return None
        finally:
            self.approval_responses.pop(request_id, None)
