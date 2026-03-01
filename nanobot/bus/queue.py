import asyncio

from nanobot.bus.events import ApprovalRequest, ApprovalResponse, InboundMessage, OutboundMessage


class MessageBus:
    def __init__(self):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        self.approval_requests: asyncio.Queue[ApprovalRequest] = asyncio.Queue()
        self.approval_responses: dict[str, asyncio.Queue[ApprovalResponse]] = {}

    async def publish_inbound(self, msg: InboundMessage) -> None:
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        return await self.outbound.get()

    async def publish_approval_request(self, req: ApprovalRequest) -> None:
        if req.id not in self.approval_responses:
            self.approval_responses[req.id] = asyncio.Queue()
            asyncio.get_event_loop().call_later(
                3600, lambda: self.approval_responses.pop(req.id, None)
            )
        await self.approval_requests.put(req)

    async def consume_approval_request(self) -> ApprovalRequest:
        return await self.approval_requests.get()

    async def publish_approval_response(self, resp: ApprovalResponse) -> None:
        if resp.id in self.approval_responses:
            await self.approval_responses[resp.id].put(resp)

    async def wait_for_approval(
        self, request_id: str, timeout: float = 300.0
    ) -> ApprovalResponse | None:
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
