from __future__ import annotations

import asyncio
import copy
import json
from contextlib import AsyncExitStack
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.auditor import CommandAuditor
from nanobot.agent.context import ContextBuilder
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.factory import register_all_tools
from nanobot.agent.tools.mcp import connect_mcp_servers
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import ChannelsConfig, ExecToolConfig
from nanobot.cron.service import CronService
from nanobot.providers.base import LLMProvider, ToolCallRequest
from nanobot.session.manager import Session, SessionManager
from nanobot.utils.database import Database
from nanobot.utils.helpers import get_model_pricing, strip_think

from ..config.schema import MCPServerConfig

if TYPE_CHECKING:
    pass


class AgentLoop:
    _TOOL_RESULT_MAX_CHARS = 4000

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 40,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        memory_window: int = 50,
        brave_api_key: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        browser_data_dir: str | None = None,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        auditor: "CommandAuditor | None" = None,
    ):
        self.bus = bus
        self.channels_config = channels_config
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.memory_window = memory_window
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self.browser_data_dir = browser_data_dir
        self.session_manager = session_manager
        self.context = ContextBuilder(workspace)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )
        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._consolidating: set[str] = set()
        self._consolidation_tasks: set[asyncio.Task] = set()
        self._consolidation_locks: dict[str, asyncio.Lock] = {}
        self._active_tasks: dict[str, list[asyncio.Task]] = {}
        self._processing_locks: dict[str, asyncio.Lock] = {}
        self.auditor = auditor
        self.db = Database(workspace)
        self.daily_budget = 5.0
        p_name = "opencode" if provider.__class__.__name__ == "OpenCodeProvider" else "auto"
        self.context.set_provider_hint(p_name)
        log_dir = self.workspace / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "nanobot.log"
        logger.add(log_file, rotation="10 MB", retention="7 days", level="DEBUG", filter="nanobot")
        logger.info("🎬 Nanobot session started. Logs: {}", log_file)
        self._register_default_tools()


    async def _periodic_cleanup(self) -> None:
        while self._running:
            await asyncio.sleep(3600)
            for k in list(self._processing_locks.keys()):
                if not self._active_tasks.get(k) and k not in self._consolidating:
                    self._processing_locks.pop(k, None)
                    self._consolidation_locks.pop(k, None)

    def _register_default_tools(self) -> None:
        register_all_tools(
            registry=self.tools,
            workspace=self.workspace,
            restrict_to_workspace=self.restrict_to_workspace,
            exec_config=self.exec_config,
            bus=self.bus,
            auditor=self.auditor,
            brave_api_key=self.brave_api_key,
            subagents=self.subagents,
            cron_service=self.cron_service,
            send_callback=self.bus.publish_outbound,
        )

    async def _connect_mcp(self) -> None:
        if self._mcp_connected or self._mcp_connecting:
            return
        if (
            "playwright" not in self._mcp_servers
            and self.provider.__class__.__name__ == "OpenCodeProvider"
        ):
            logger.info("Auto-configuring Playwright MCP for OpenCode intelligence engine...")
            args = ["-y", "@playwright/mcp", "--headless"]
            if self.browser_data_dir:
                args = ["-y", "@playwright/mcp", "--user-data-dir", self.browser_data_dir]
            self._mcp_servers["playwright"] = MCPServerConfig(command="npx", args=args)
        if not self._mcp_servers:
            return
        self._mcp_connecting = True
        try:
            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)
            self._mcp_connected = True
        except Exception as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except Exception:
                    pass
                self._mcp_stack = None
        finally:
            self._mcp_connecting = False

    def _set_tool_context(
        self,
        channel: str,
        chat_id: str,
        message_id: str | None = None,
        thread_ts: str | None = None,
    ) -> None:
        for name in ("message", "spawn", "cron"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    if name == "message":
                        tool.set_context(channel, chat_id, message_id)
                    elif name == "spawn":
                        tool.set_context(channel, chat_id, thread_ts=thread_ts)
                    else:
                        tool.set_context(channel, chat_id)

    @staticmethod
    def _tool_hint(tool_calls: list[ToolCallRequest]) -> str:
        def tc_hint(tc: ToolCallRequest) -> str:
            args = tc.arguments
            name = tc.name
            if name == "read_file":
                return f"📖 Reading {args.get('path', 'file')}"
            if name == "write_file":
                return f"📝 Writing {args.get('path', 'file')}"
            if name == "edit_file":
                return f"🔨 Editing {args.get('path', 'file')}"
            if name in ("rewrite_code", "rewrite_file"):
                return f"🧬 Refactoring {args.get('path', 'file')}"
            if name == "exec":
                cmd = args.get("command", "")
                display_cmd = cmd[:40] + "..." if len(cmd) > 40 else cmd
                return f"⌨️ Executing: {display_cmd}"
            if name == "web_search":
                return f"🌐 Searching web: {args.get('query', '...')}"
            if name == "browser_navigate":
                return f"🌍 Navigating to {args.get('url', '...')}"
            if name == "manage_tasks":
                action = args.get("action", "manage")
                return f"📋 Task Board: {action} {args.get('id', '')}"
            if name == "add_job":
                return f"⏰ Scheduling: {args.get('name', 'reminder')}"
            if name == "spawn_agent":
                return f"🤖 Spawning sub-agent: {args.get('name', 'helper')}"
            return f"⚙️ {name}({next(iter(args.values()), '...') if args else '...'})"

        return " | ".join((tc_hint(tc) for tc in tool_calls))

    async def _execute_single_tool(
        self, tc: Any, metadata: dict, tools_used: list[str]
    ) -> tuple[str, str, str]:
        tools_used.append(tc.name)
        channel = metadata.get("channel")
        chat_id = metadata.get("chat_id")
        args_str = json.dumps(tc.arguments, ensure_ascii=False)
        logger.info("🔧 Tool start: {}({})", tc.name, args_str[:500])
        if on_progress := metadata.get("on_progress"):
             await on_progress(f"🔧 Tool start: {tc.name}")
        result = await self.tools.execute(
            tc.name,
            tc.arguments,
            workspace=self.workspace,
            channel=channel,
            chat_id=chat_id,
            on_progress=on_progress,
            metadata={"slack": {"thread_ts": metadata.get("thread_ts")}},
            outbound_msg_factory=lambda content: OutboundMessage(
                channel=channel, chat_id=chat_id, content=content
            ),
        )
        if isinstance(result, (dict, list)):
            res_str = json.dumps(result, ensure_ascii=False, indent=2)
        else:
            res_str = str(result)
        logger.info("✅ Tool finish: {} -> {}", tc.name, res_str[:200] + "..." if len(res_str) > 200 else res_str)
        if on_progress := metadata.get("on_progress"):
             await on_progress(f"✅ Tool finish: {tc.name}")
        return (tc.id, tc.name, res_str)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        session_key: str = "default",
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> tuple[str | None, list[str], list[dict]]:
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []
        while iteration < self.max_iterations:
            iteration += 1
            daily_total = self.db.get_daily_cost()
            if daily_total > self.daily_budget:
                logger.critical(
                    "DAILY BUDGET EXCEEDED: ${:.2f} / ${:.2f}", daily_total, self.daily_budget
                )
                final_content = f"⚠️ **Budget Exceeded**: Daily usage (${daily_total:.2f}) has reached your limit of ${self.daily_budget:.2f}. Increase the limit or wait until tomorrow."
                messages = self.context.add_assistant_message(messages, final_content)
                return (final_content, tools_used, messages)
            if on_progress:
                await on_progress(f"🧠 Iteration {iteration}: Thinking...")
            logger.info("🧠 Iteration {}: Calling provider ({})", iteration, self.model)
            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                on_progress=on_progress,
            )
            cost = 0.0
            if response.usage:
                p_tokens = response.usage.get("prompt_tokens", 0)
                c_tokens = response.usage.get("completion_tokens", 0)
                input_rate, output_rate = get_model_pricing(self.model)
                cost = p_tokens / 1000000.0 * input_rate + c_tokens / 1000000.0 * output_rate
                self.db.log_cost(
                    session_key,
                    self.provider.__class__.__name__,
                    self.model,
                    p_tokens,
                    c_tokens,
                    cost,
                )
            logger.info("🧠 Iteration {}: Received response ({} tokens, cost: ${:.4f})", iteration, response.usage.get("total_tokens", 0) if response.usage else 0, cost)

            if response.has_tool_calls:
                if on_progress:
                    clean = strip_think(response.content)
                    if clean and not response.streamed:
                        await on_progress(clean)
                    await on_progress(f"⚙️ {self._tool_hint(response.tool_calls)}", tool_hint=True)
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
                messages = self.context.add_assistant_message(
                    messages,
                    response.content,
                    tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                )
                metadata = initial_messages[0].get("_nanobot_metadata", {})
                metadata["on_progress"] = on_progress
                results = await asyncio.gather(
                    *[
                        self._execute_single_tool(tc, metadata, tools_used)
                        for tc in response.tool_calls
                    ]
                )
                for tc_id, tc_name, tc_result in results:
                    messages = self.context.add_tool_result(messages, tc_id, tc_name, tc_result)
            else:
                clean = strip_think(response.content)
                messages = self.context.add_assistant_message(
                    messages, clean, reasoning_content=response.reasoning_content
                )
                final_content = clean
                break
        if final_content is None and iteration >= self.max_iterations:
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            final_content = f"Max iterations ({self.max_iterations}) reached without completion. Try breaking the task into smaller steps."
            messages = self.context.add_assistant_message(messages, final_content)
        return (final_content, tools_used, messages)

    async def run(self) -> None:
        self._running = True
        asyncio.create_task(self._periodic_cleanup())
        await self._connect_mcp()
        logger.info("Agent loop started")
        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if msg.content.strip().lower() == "/stop":
                await self._handle_stop(msg)
            elif (cmd_lower := msg.content.strip().lower()).startswith(
                ("/rollback", "/git-rollback")
            ):
                await self._handle_rollback(msg, cmd_lower)
            else:
                task = asyncio.create_task(self._dispatch(msg))
                self._active_tasks.setdefault(msg.session_key, []).append(task)
                task.add_done_callback(
                    lambda t, k=msg.session_key: (
                        self._active_tasks.get(k, []).remove(t)
                        if t in self._active_tasks.get(k, [])
                        else None
                    )
                )

    async def _handle_stop(self, msg: InboundMessage) -> None:
        tasks = self._active_tasks.pop(msg.session_key, [])
        cancelled = sum((1 for t in tasks if not t.done() and t.cancel()))
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        sub_cancelled = await self.subagents.cancel_by_session(msg.session_key)
        total = cancelled + sub_cancelled
        content = f"⏹ Stopped {total} task(s)." if total else "No active tasks to stop."
        await self.bus.publish_outbound(
            OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=content)
        )

    async def _handle_rollback(self, msg: InboundMessage, cmd: str) -> None:
        import subprocess

        parts = cmd.split()
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
        try:
            subprocess.run(
                ["git", "reset", "--hard", f"HEAD~{n}"],
                cwd=str(self.workspace),
                check=True,
                capture_output=True,
            )
            content = f"⏪ Rolled back to {n} commit(s) ago."
        except Exception as e:
            content = f"❌ Rollback failed: {e}"
        await self.bus.publish_outbound(
            OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=content)
        )

    async def _dispatch(self, msg: InboundMessage) -> None:
        lock = self._processing_locks.setdefault(msg.session_key, asyncio.Lock())
        async with lock:
            if asyncio.current_task().cancelled():
                logger.info("Task cancelled before processing session {}", msg.session_key)
                return
            try:
                logger.info("📩 Processing message from {}:{} (session: {})", msg.channel, msg.sender_id, msg.session_key)
                response = await self._process_message(msg)
                if response is not None:
                    await self.bus.publish_outbound(response)
                elif msg.channel == "cli":
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content="",
                            metadata=msg.metadata or {},
                        )
                    )
            except asyncio.CancelledError:
                logger.info("Task cancelled for session {}", msg.session_key)
                raise
            except Exception:
                logger.exception("Error processing message for session {}", msg.session_key)
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="Sorry, I encountered an error.",
                    )
                )

    async def close_mcp(self) -> None:
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass
            self._mcp_stack = None
            self._mcp_connected = False
            self._mcp_connecting = False

    def stop(self) -> None:
        self._running = False
        logger.info("Agent loop stopping")

    def _get_active_state(self) -> str | None:
        parts = []
        if self.cron_service:
            jobs = self.cron_service.list_jobs()
            if jobs:
                parts.append("## Active Cron Jobs")
                for j in jobs:
                    parts.append(f"- {j.name} (id: {j.id}, type: {j.schedule.kind})")

        tasks_dir = self.workspace / "tasks"
        if tasks_dir.exists():
            tasks = []
            for f in tasks_dir.glob("*.json"):
                try:
                    task = json.loads(f.read_text())
                    if task.get("status") != "done":
                        tasks.append(task)
                except Exception:
                    pass
            if tasks:
                if parts:
                    parts.append("")
                parts.append("## Active Tasks (from manage_tasks)")
                for t in tasks:
                    parts.append(f"- [{t.get('status')}] {t.get('title')} (id: {t.get('id')})")
        return "\n".join(parts) if parts else None

    async def _handle_system_message(self, msg: InboundMessage) -> OutboundMessage:
        meta = msg.metadata or {}
        channel = meta.get("origin_channel", "cli")
        chat_id = msg.chat_id
        logger.info("Processing system message from {}", msg.sender_id)
        key = f"{channel}:{chat_id}"
        session = self.sessions.get_or_create(key)
        self._set_tool_context(
            channel,
            chat_id,
            msg.metadata.get("message_id"),
            msg.metadata.get("thread_ts"),
        )
        logger.info("🏗 Building context for system message session {}", key)
        history = session.get_history(max_messages=self.memory_window)
        messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            channel=channel,
            chat_id=chat_id,
            extra_context=self._get_active_state(),
        )
        messages[0]["_nanobot_metadata"] = {
            "channel": channel,
            "chat_id": chat_id,
            "message_id": msg.metadata.get("message_id"),
        }
        final_content, _, all_msgs = await self._run_agent_loop(messages, session_key=session.key)
        self._save_turn(session, all_msgs, len(messages) - 1)
        await self.sessions.save_async(session)
        return OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=final_content or "Background task completed.",
            metadata={"thread_ts": msg.metadata.get("thread_ts")},
        )

    async def _bus_progress(
        self, msg: InboundMessage, content: str, *, tool_hint: bool = False
    ) -> None:
        meta = dict(msg.metadata or {})
        meta["_progress"] = True
        meta["_tool_hint"] = tool_hint
        await self.bus.publish_outbound(
            OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=meta
            )
        )

    async def _handle_new_command(self, session: Session, msg: InboundMessage) -> OutboundMessage:
        lock = self._consolidation_locks.setdefault(session.key, asyncio.Lock())
        self._consolidating.add(session.key)
        try:
            async with lock:
                snapshot = session.messages[session.last_consolidated :]
                if snapshot:
                    temp = Session(key=session.key)
                    temp.messages = list(snapshot)
                    if not await self.context.memory.consolidate(
                        temp, self.provider, self.model, archive_all=True
                    ):
                        return OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content="Memory archival failed. Session remains active.",
                        )
        except Exception:
            logger.exception("/new archival failed for {}", session.key)
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="Memory archival failed. Session remains active.",
            )
        finally:
            self._consolidating.discard(session.key)
        session.clear()
        await self.sessions.save_async(session)
        self.sessions.invalidate(session.key)
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content="Session cleared."
        )

    async def _do_consolidation(self, session: Session) -> None:
        lock = self._consolidation_locks.setdefault(session.key, asyncio.Lock())
        try:
            async with lock:
                if await self.context.memory.consolidate(
                    session, self.provider, self.model, memory_window=self.memory_window
                ):
                    latest_session = self.sessions.get_or_create(session.key)
                    if latest_session is not session:
                        latest_session.last_consolidated = max(
                            latest_session.last_consolidated, session.last_consolidated
                        )
                    try:
                        await self.sessions.save_async(latest_session)
                    except Exception as e:
                        logger.error("Failed to save session {}: {}", latest_session.key, e)
        except Exception as e:
            logger.error("Error during memory consolidation: {}", e)
        finally:
            self._consolidating.discard(session.key)
            _task = asyncio.current_task()
            if _task is not None:
                self._consolidation_tasks.discard(_task)

    def _check_consolidation(self, session: Session) -> None:
        unconsolidated = len(session.messages) - session.last_consolidated
        if unconsolidated >= self.memory_window and session.key not in self._consolidating:
            self._consolidating.add(session.key)
            _task = asyncio.create_task(self._do_consolidation(session))
            self._consolidation_tasks.add(_task)

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        if msg.channel == "system":
            return await self._handle_system_message(msg)
        logger.info(
            "📩 Processing inbound message from {}:{}: {}",
            msg.channel,
            msg.sender_id,
            msg.content[:100] + "..." if len(msg.content) > 100 else msg.content,
        )
        session = self.sessions.get_or_create(session_key or msg.session_key)
        is_godmode = msg.metadata.get("is_godmode", False)
        if is_godmode:
            logger.warning("🚨 GOD MODE active for session {}", session.key)
            god_prompt = "\n\n--- [SYSTEM: GOD MODE] ---\nYou have full permission to modify your source code. 1. Research relevant files. 2. Implement changes. 3. Verify with `ruff check nanobot`. 4. Call `reload_agent`. Do not fail."
            msg.content += god_prompt
        cmd = msg.content.strip().lower()
        if cmd == "/new":
            return await self._handle_new_command(session, msg)
        if cmd == "/help":
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="🤖 nanobot commands:\n/new — Clear session\n/stop — Stop active tasks\n/help — Show commands",
            )
        self._check_consolidation(session)
        self._set_tool_context(
            msg.channel,
            msg.chat_id,
            msg.metadata.get("message_id"),
            msg.metadata.get("thread_ts"),
        )
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()
        history = session.get_history(max_messages=self.memory_window)
        logger.info("🏗 Building context ({} history messages) for session {}", len(history), session.key)
        initial_messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
            extra_context=self._get_active_state(),
        )
        initial_messages[0]["_nanobot_metadata"] = {
            "channel": msg.channel,
            "chat_id": msg.chat_id,
            "thread_ts": msg.metadata.get("thread_ts"),
            "message_id": msg.metadata.get("message_id"),
        }
        try:
            final_content, _, all_msgs = await self._run_agent_loop(
                initial_messages,
                session_key=session.key,
                on_progress=on_progress
                or (lambda c, **k: self._bus_progress(msg, c, tool_hint=k.get("tool_hint", False))),
            )
            if final_content is None:
                final_content = "Task completed. No further output."
        except Exception as e:
            logger.exception("Error during agent loop iteration")
            final_content = f"Execution error: {e}"
            all_msgs = initial_messages
        self._save_turn(session, all_msgs, 1 + len(history))
        await self.sessions.save_async(session)
        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            return None
        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("📤 Sending final response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            metadata=msg.metadata or {},
        )

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        for m in messages[skip:]:
            entry = copy.deepcopy(m)
            role, content = entry.get("role"), entry.get("content")
            if role == "tool" and isinstance(content, str) and len(content) > 4000:
                entry["content"] = content[:4000] + "\n... (truncated)"
            elif role == "user":
                if isinstance(content, str) and ContextBuilder._RUNTIME_CONTEXT_TAG in content:
                    parts = content.split("\n\n", 1)
                    if len(parts) == 2 and parts[0].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                        entry["content"] = parts[1]
                    elif parts[0].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                        entry["content"] = ""
                elif isinstance(content, list):
                    entry["content"] = [
                        (
                            {"type": "text", "text": "[image]"}
                            if c.get("type") == "image_url"
                            and c.get("image_url", {}).get("url", "").startswith("data:image/")
                            else {"type": "text", "text": c.get("text", "").split("\n\n", 1)[1]}
                            if c.get("type") == "text"
                            and ContextBuilder._RUNTIME_CONTEXT_TAG in c.get("text", "")
                            and c.get("text", "")
                            .split("\n\n", 1)[0]
                            .startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)
                            else c
                        )
                        for c in content
                        if c
                    ]
            if role == "assistant" and entry.get("tool_calls"):
                entry["tools_used"] = [
                    tc.get("function", {}).get("name", "unknown")
                    if isinstance(tc, dict)
                    else getattr(tc, "name", "unknown")
                    for tc in entry["tool_calls"]
                ]
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now()

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        await self._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)
        lock = self._processing_locks.setdefault(session_key, asyncio.Lock())
        async with lock:
            response = await self._process_message(
                msg, session_key=session_key, on_progress=on_progress
            )
            return response.content if response else ""
