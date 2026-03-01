import asyncio

import pytest

from nanobot.heartbeat.service import HeartbeatService
from nanobot.providers.base import LLMResponse


class DummyProvider:
    def __init__(self, responses: list[LLMResponse]):
        self._responses = list(responses)

    async def chat(self, *args, **kwargs) -> LLMResponse:
        if self._responses:
            return self._responses.pop(0)
        return LLMResponse(content="", tool_calls=[])


@pytest.mark.asyncio
async def test_start_is_idempotent(tmp_path) -> None:
    provider = DummyProvider([])
    service = HeartbeatService(
        workspace=tmp_path,
        provider=provider,
        model="openai/gpt-4o-mini",
        interval_s=9999,
        enabled=True,
    )
    await service.start()
    first_task = service._task
    await service.start()
    assert service._task is first_task
    service.stop()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_decide_returns_skip_when_no_tool_call(tmp_path) -> None:
    provider = DummyProvider([LLMResponse(content="no tool call", tool_calls=[])])
    service = HeartbeatService(workspace=tmp_path, provider=provider, model="openai/gpt-4o-mini")
    action, tasks = await service._decide("heartbeat content")
    assert action == "skip"
    assert tasks == ""
