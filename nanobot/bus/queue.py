import asyncio

from loguru import logger

from nanobot.bus.events import ApprovalRequest, ApprovalResponse, InboundMessage, OutboundMessage


class MessageBus:
    def __init__(self, db=None):
        self.db = db
        self.inbound: asyncio.PriorityQueue[tuple[int, int, InboundMessage]] = (
            asyncio.PriorityQueue()
        )
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        self.approval_requests: asyncio.Queue[ApprovalRequest] = asyncio.Queue()
        self.approval_responses: dict[str, asyncio.Queue[ApprovalResponse]] = {}
        self._seq = 0

    async def publish_inbound(self, msg: InboundMessage) -> None:
        self._seq += 1
        if self.db:
            self.db.log_trace(msg.session_key, "inbound", msg.__dict__)
        await self.inbound.put((msg.priority, self._seq, msg))

    async def consume_inbound(self) -> InboundMessage:
        _, _, msg = await self.inbound.get()
        return msg

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        if self.db:
            self.db.log_trace(f"{msg.channel}:{msg.chat_id}", "outbound", msg.__dict__)
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        return await self.outbound.get()

    async def publish_approval_request(self, req: ApprovalRequest) -> None:
        if self.db:
            self.db.log_trace(f"{req.channel}:{req.chat_id}", "approval_request", req.__dict__)
        if req.id not in self.approval_responses:
            self.approval_responses[req.id] = asyncio.Queue()
            loop = asyncio.get_running_loop()
            loop.call_later(3600, lambda: self.approval_responses.pop(req.id, None))
        await self.approval_requests.put(req)

    async def consume_approval_request(self) -> ApprovalRequest:
        return await self.approval_requests.get()

    async def publish_approval_response(self, resp: ApprovalResponse) -> None:
        if self.db:
            self.db.log_trace(resp.id, "approval_response", resp.__dict__)
        if resp.id in self.approval_responses:
            await self.approval_responses[resp.id].put(resp)
        else:
            logger.warning("Approval response {} dropped: no pending request", resp.id)

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
