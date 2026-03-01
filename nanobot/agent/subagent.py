import asyncio
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.auditor import CommandAuditor
from nanobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import ExecToolConfig
from nanobot.providers.base import LLMProvider
from nanobot.utils.database import Database
from nanobot.utils.helpers import get_model_pricing, strip_think

if TYPE_CHECKING:
    pass


class SubagentManager:
    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        brave_api_key: str | None = None,
        exec_config: "ExecToolConfig | None" = None,
        restrict_to_workspace: bool = False,
        auditor: "CommandAuditor | None" = None,
    ):
        self.provider = provider
        self.workspace = workspace
        self.bus = bus
        self.model = model or provider.get_default_model()
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.restrict_to_workspace = restrict_to_workspace
        self._auditor = auditor or CommandAuditor(provider=self.provider, model=self.model)
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._session_tasks: dict[str, set[str]] = {}
        self.max_iterations = 15
        self.db = Database(workspace)
        self.daily_budget = 5.0

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
    ) -> str:
        task_id = str(uuid.uuid4())[:8]
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")
        origin = {"channel": origin_channel, "chat_id": origin_chat_id}
        bg_task = asyncio.create_task(self._run_subagent(task_id, task, display_label, origin))
        self._running_tasks[task_id] = bg_task
        if session_key:
            self._session_tasks.setdefault(session_key, set()).add(task_id)

        def _cleanup(_: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            if session_key and (ids := self._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[session_key]

        bg_task.add_done_callback(_cleanup)
        logger.info("Spawned subagent [{}]: {}", task_id, display_label)
        return f"Subagent [{display_label}] started (id: {task_id}). I'll notify you when it completes."

    async def _run_subagent(
        self, task_id: str, task: str, label: str, origin: dict[str, str]
    ) -> None:
        logger.info("Subagent [{}] starting task: {}", task_id, label)
        try:
            tools = ToolRegistry()
            allowed_dir = self.workspace if self.restrict_to_workspace else None
            tools.register(ReadFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(WriteFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(EditFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(ListDirTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(
                ExecTool(
                    working_dir=str(self.workspace),
                    timeout=self.exec_config.timeout,
                    restrict_to_workspace=self.restrict_to_workspace,
                    path_append=self.exec_config.path_append,
                    auditor=self._auditor,
                    bus=self.bus,
                )
            )
            tools.register(WebSearchTool(api_key=self.brave_api_key))
            tools.register(WebFetchTool())
            system_prompt = self._build_subagent_prompt(task)
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ]
            max_iterations = self.max_iterations
            iteration = 0
            final_result: str | None = None
            while iteration < max_iterations:
                iteration += 1
                daily_total = self.db.get_daily_cost()
                if daily_total > self.daily_budget:
                    logger.critical(
                        "SUBAGENT BUDGET EXCEEDED: ${:.2f} / ${:.2f}",
                        daily_total,
                        self.daily_budget,
                    )
                    final_result = f"⚠️ **Budget Exceeded**: Daily usage (${daily_total:.2f}) has exceeded your limit of ${self.daily_budget:.2f}. Subagent stopping."
                    break
                response = await self.provider.chat(
                    messages=messages,
                    tools=tools.get_definitions(),
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
                if response.usage:
                    p_tokens = response.usage.get("prompt_tokens", 0)
                    c_tokens = response.usage.get("completion_tokens", 0)
                    input_rate, output_rate = get_model_pricing(self.model)
                    cost = p_tokens / 1000000.0 * input_rate + c_tokens / 1000000.0 * output_rate
                    self.db.log_cost(
                        f"subagent:{task_id}",
                        self.provider.__class__.__name__,
                        self.model,
                        p_tokens,
                        c_tokens,
                        cost,
                    )
                if response.has_tool_calls:
                    tool_call_dicts = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
                        for tc in response.tool_calls
                    ]
                    messages.append(
                        {
                            "role": "assistant",
                            "content": response.content or "",
                            "tool_calls": tool_call_dicts,
                        }
                    )
                    results = await asyncio.gather(
                        *[
                            tools.execute(
                                tc.name,
                                tc.arguments,
                                channel=origin["channel"],
                                chat_id=origin["chat_id"],
                            )
                            for tc in response.tool_calls
                        ]
                    )
                    for tool_call, result in zip(response.tool_calls, results, strict=False):
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "name": tool_call.name,
                                "content": result,
                            }
                        )
                else:
                    final_result = strip_think(response.content)
                    break
            if final_result is None:
                final_result = "Task completed but no final response was generated."
            logger.info("Subagent [{}] completed successfully", task_id)
            await self._announce_result(task_id, label, task, final_result, origin, "ok")
        except Exception as e:
            error_msg = f"Error: {str(e)}"
            logger.error("Subagent [{}] failed: {}", task_id, e)
            await self._announce_result(task_id, label, task, error_msg, origin, "error")

    async def _announce_result(
        self, task_id: str, label: str, task: str, result: str, origin: dict[str, str], status: str
    ) -> None:
        status_text = "completed successfully" if status == "ok" else "failed"
        announce_content = f"""[Subagent '{label}' {status_text}]\n\nTask: {task}\n\nResult:\n{result}\n\nSummarize this naturally for the user. Keep it brief (1-2 sentences). Do not mention technical details like "subagent" or task IDs."""
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
        )
        await self.bus.publish_inbound(msg)
        logger.debug(
            "Subagent [{}] announced result to {}:{}", task_id, origin["channel"], origin["chat_id"]
        )

    def _build_subagent_prompt(self, task: str) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = time.strftime("%Z") or "UTC"
        return f"# Subagent\n\n## Current Time\n{now} ({tz})\n\nYou are a subagent spawned by the main agent to complete a specific task.\n\n## Rules\n1. Stay focused - complete only the assigned task, nothing else\n2. Your final response will be reported back to the main agent\n3. Do not initiate conversations or take on side tasks\n4. Be concise but informative in your findings\n\n## What You Can Do\n- Read and write files in the workspace\n- Execute shell commands\n- Search the web and fetch web pages\n- Complete the task thoroughly\n\n## What You Cannot Do\n- Send messages directly to users (no message tool available)\n- Spawn other subagents\n- Access the main agent's conversation history\n\n## Workspace\nYour workspace is at: {self.workspace}\nSkills are available at: {self.workspace}/skills/ (read SKILL.md files as needed)\n\nWhen you have completed the task, provide a clear summary of your findings or actions."

    async def cancel_by_session(self, session_key: str) -> int:
        tasks = [
            self._running_tasks[tid]
            for tid in self._session_tasks.get(session_key, [])
            if tid in self._running_tasks and (not self._running_tasks[tid].done())
        ]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks)
