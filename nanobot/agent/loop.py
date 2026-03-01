"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.auditor import CommandAuditor
from nanobot.agent.context import ContextBuilder
from nanobot.agent.memory import MemoryStore
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.system import ReloadTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider, ToolCallRequest
from nanobot.session.manager import Session, SessionManager
from nanobot.utils.database import Database

if TYPE_CHECKING:
    from nanobot.config.schema import ChannelsConfig, ExecToolConfig
    from nanobot.cron.service import CronService


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

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
        memory_window: int = 100,
        brave_api_key: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        auditor: "CommandAuditor | None" = None,
    ):
        from nanobot.config.schema import ExecToolConfig

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
        self._consolidating: set[str] = set()  # Session keys with consolidation in progress
        self._consolidation_tasks: set[asyncio.Task] = set()  # Strong refs to in-flight tasks
        self._consolidation_locks: dict[str, asyncio.Lock] = {}
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._processing_locks: dict[str, asyncio.Lock] = {}
        self.auditor = auditor
        self.db = Database(workspace)
        self.daily_budget = 5.0  # Default $5.00 daily budget

        # Periodic lock cleanup
        self._cleanup_task: asyncio.Task | None = None

        # Inject provider hint into context builder
        p_name = "opencode" if provider.__class__.__name__ == "OpenCodeProvider" else "auto"
        self.context.set_provider_hint(p_name)

        self._register_default_tools()

    async def _periodic_cleanup(self) -> None:
        """Periodic cleanup of stale session locks."""
        while self._running:
            await asyncio.sleep(3600)  # Hourly
            # Only remove locks if no active tasks for that session
            keys_to_remove = [
                k
                for k in self._processing_locks.keys()
                if not self._active_tasks.get(k) and k not in self._consolidating
            ]
            for k in keys_to_remove:
                self._processing_locks.pop(k, None)
                self._consolidation_locks.pop(k, None)

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(
            ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.restrict_to_workspace,
                path_append=self.exec_config.path_append,
                bus=self.bus,
                auditor=self.auditor,
            )
        )
        self.tools.register(WebSearchTool(api_key=self.brave_api_key))
        self.tools.register(WebFetchTool())
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        self.tools.register(SpawnTool(manager=self.subagents))
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))
        self.tools.register(ReloadTool())

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or self._mcp_connecting:
            return

        # Automatic Playwright MCP support for OpenCode intelligence engine
        if (
            "playwright" not in self._mcp_servers
            and self.provider.__class__.__name__ == "OpenCodeProvider"
        ):
            from ..config.schema import MCPServerConfig

            logger.info("Auto-configuring Playwright MCP for OpenCode intelligence engine...")
            self._mcp_servers["playwright"] = MCPServerConfig(
                command="npx", args=["-y", "@playwright/mcp", "--headless"]
            )

        if not self._mcp_servers:
            return

        self._mcp_connecting = True
        from nanobot.agent.tools.mcp import connect_mcp_servers

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

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Update context for all tools that need routing info."""
        for name in ("message", "spawn", "cron"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    tool.set_context(channel, chat_id, *([message_id] if name == "message" else []))

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> blocks that some models embed in content."""
        if not text:
            return None
        return re.sub(r"<think>[\s\S]*?</think>", "", text).strip() or None

    @staticmethod
    def _tool_hint_str(tc: ToolCallRequest) -> str:
        args = tc.arguments
        if tc.name in ("read_file", "write_file", "edit_file"):
            return f"{tc.name}({args.get('path', '...')})"
        if tc.name == "exec":
            cmd = args.get("command", "")
            return f"exec({cmd[:30]}...)" if len(cmd) > 30 else f"exec({cmd})"
        val = next(iter(args.values()), "...") if args else "..."
        return f"{tc.name}({val})"

    @staticmethod
    def _tool_hint(tool_calls: list[ToolCallRequest]) -> str:
        return ", ".join(AgentLoop._tool_hint_str(tc) for tc in tool_calls)

    async def _execute_single_tool(
        self, tc: Any, metadata: dict, tools_used: list[str]
    ) -> tuple[str, str, str]:
        tools_used.append(tc.name)
        channel = metadata.get("channel")
        chat_id = metadata.get("chat_id")
        args_str = json.dumps(tc.arguments, ensure_ascii=False)
        logger.info("Tool call: {}({})", tc.name, args_str[:200])
        result = await self.tools.execute(
            tc.name,
            tc.arguments,
            channel=channel,
            chat_id=chat_id,
            metadata={"slack": {"thread_ts": metadata.get("thread_ts")}},
            outbound_msg_factory=lambda content: OutboundMessage(
                channel=channel, chat_id=chat_id, content=content
            ),
        )
        return tc.id, tc.name, str(result)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        session_key: str = "default",
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> tuple[str | None, list[str], list[dict]]:
        """Run the agent iteration loop. Returns (final_content, tools_used, messages)."""
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []

        while iteration < self.max_iterations:
            iteration += 1

            # Budget enforcement (check before next LLM call)
            daily_total = self.db.get_daily_cost()
            if daily_total > self.daily_budget:
                logger.critical(
                    "DAILY BUDGET EXCEEDED: ${:.2f} / ${:.2f}", daily_total, self.daily_budget
                )
                final_content = (
                    f"⚠️ **Budget Exceeded**: Daily usage (${daily_total:.2f}) has exceeded your limit of ${self.daily_budget:.2f}. "
                    "Please increase the limit or wait until tomorrow."
                )
                messages = self.context.add_assistant_message(messages, final_content)
                return final_content, tools_used, messages

            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                on_progress=on_progress,
            )

            # Log tokens and cost
            if response.usage:
                from nanobot.utils.helpers import get_model_pricing

                p_tokens = response.usage.get("prompt_tokens", 0)
                c_tokens = response.usage.get("completion_tokens", 0)
                # Model-specific pricing (separate input/output rates)
                input_rate, output_rate = get_model_pricing(self.model)
                cost = p_tokens / 1_000_000.0 * input_rate + c_tokens / 1_000_000.0 * output_rate
                self.db.log_cost(
                    session_key,
                    self.provider.__class__.__name__,
                    self.model,
                    p_tokens,
                    c_tokens,
                    cost,
                )

            if response.has_tool_calls:
                if on_progress:
                    clean = self._strip_think(response.content)
                    if clean:
                        await on_progress(clean)
                    await on_progress(self._tool_hint(response.tool_calls), tool_hint=True)

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

                results = await asyncio.gather(
                    *[
                        self._execute_single_tool(tc, metadata, tools_used)
                        for tc in response.tool_calls
                    ]
                )

                for tc_id, tc_name, tc_result in results:
                    messages = self.context.add_tool_result(messages, tc_id, tc_name, tc_result)
            else:
                clean = self._strip_think(response.content)
                messages = self.context.add_assistant_message(
                    messages,
                    clean,
                    reasoning_content=response.reasoning_content,
                )
                final_content = clean
                break

        if final_content is None and iteration >= self.max_iterations:
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            final_content = (
                f"I reached the maximum number of tool call iterations ({self.max_iterations}) "
                "without completing the task. You can try breaking the task into smaller steps."
            )
            messages = self.context.add_assistant_message(messages, final_content)

        return final_content, tools_used, messages

    def _on_task_done(self, t: asyncio.Task, key: str) -> None:
        tasks = self._active_tasks.get(key)
        if tasks and t in tasks:
            tasks.remove(t)

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
        await self._connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if msg.content.strip().lower() == "/stop":
                await self._handle_stop(msg)
            else:
                task = asyncio.create_task(self._dispatch(msg))
                self._active_tasks.setdefault(msg.session_key, []).append(task)
                task.add_done_callback(lambda t, key=msg.session_key: self._on_task_done(t, key))

    async def _handle_stop(self, msg: InboundMessage) -> None:
        """Cancel all active tasks and subagents for the session."""
        tasks = self._active_tasks.pop(msg.session_key, [])
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        sub_cancelled = await self.subagents.cancel_by_session(msg.session_key)
        total = cancelled + sub_cancelled
        content = f"⏹ Stopped {total} task(s)." if total else "No active task to stop."
        await self.bus.publish_outbound(
            OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=content,
            )
        )

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message under the session-specific lock."""
        lock = self._processing_locks.setdefault(msg.session_key, asyncio.Lock())
        async with lock:
            if asyncio.current_task().cancelled():
                logger.info("Task cancelled before processing session {}", msg.session_key)
                return
            try:
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
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None
            self._mcp_connected = False
            self._mcp_connecting = False

    def stop(self) -> None:
        self._running = False
        logger.info("Agent loop stopping")

    async def _handle_system_message(self, msg: InboundMessage) -> OutboundMessage:
        """Handle internal system messages from background tasks/cron."""
        channel, chat_id = msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id)
        logger.info("Processing system message from {}", msg.sender_id)
        key = f"{channel}:{chat_id}"
        session = self.sessions.get_or_create(key)
        self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"))
        history = session.get_history(max_messages=self.memory_window)
        messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            channel=channel,
            chat_id=chat_id,
        )
        # Inject metadata for tool call routing
        messages[0]["_nanobot_metadata"] = {
            "channel": channel,
            "chat_id": chat_id,
            "message_id": msg.metadata.get("message_id"),
        }
        final_content, _, all_msgs = await self._run_agent_loop(messages, session_key=session.key)
        self._save_turn(
            session, all_msgs, len(messages) - 1
        )  # Correct skip: current user msg and context were added
        await self.sessions.save_async(session)
        return OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=final_content or "Background task completed.",
        )

    async def _bus_progress(
        self, msg: InboundMessage, content: str, *, tool_hint: bool = False
    ) -> None:
        meta = dict(msg.metadata or {})
        meta["_progress"] = True
        meta["_tool_hint"] = tool_hint
        await self.bus.publish_outbound(
            OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=content,
                metadata=meta,
            )
        )

    async def _handle_new_command(self, session: Session, msg: InboundMessage) -> OutboundMessage:
        """Handle the /new slash command."""
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
                            content="Memory archival failed, session not cleared. Please try again.",
                        )
        except Exception:
            logger.exception("/new archival failed for {}", session.key)
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="Memory archival failed, session not cleared. Please try again.",
            )
        finally:
            self._consolidating.discard(session.key)

        session.clear()
        await self.sessions.save_async(session)
        self.sessions.invalidate(session.key)
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content="New session started."
        )

    async def _do_consolidation(self, session: Session) -> None:
        lock = self._consolidation_locks.setdefault(session.key, asyncio.Lock())
        try:
            async with lock:
                if await self.context.memory.consolidate(
                    session,
                    self.provider,
                    self.model,
                    memory_window=self.memory_window,
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
            "Processing message from {}:{}: {}",
            msg.channel,
            msg.sender_id,
            msg.content[:80] + "..." if len(msg.content) > 80 else msg.content,
        )

        session = self.sessions.get_or_create(session_key or msg.session_key)

        # God Mode check
        is_godmode = msg.metadata.get("is_godmode", False)
        if is_godmode:
            logger.warning("🚨 GOD MODE active for session {}", session.key)
            god_prompt = (
                "\n\n--- [SYSTEM NOTICE: GOD MODE ACTIVE] ---\n"
                "You have been granted full permissions to modify your own source code accurately. "
                "1. Research your implementation by reading files in your workspace.\n"
                "2. Implement the requested changes.\n"
                "3. Use `ruff check nanobot` or other shell commands to verify syntax.\n"
                "4. Call `reload_agent` to restart and apply changes.\n"
                "DO NOT fail. If you break the code, you will stop functioning."
            )
            # Inject into msg.content so it's part of the current user turn
            msg.content += god_prompt

        # Slash commands
        cmd = msg.content.strip().lower()
        if cmd == "/new":
            return await self._handle_new_command(session, msg)
        if cmd == "/help":
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="🐈 nanobot commands:\n/new — Start a new conversation\n/stop — Stop the current task\n/help — Show available commands",
            )

        self._check_consolidation(session)

        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        history = session.get_history(max_messages=self.memory_window)
        initial_messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
        )
        # Inject metadata for tool call routing
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
                final_content = "I've completed processing but have no response to give."
        except Exception as e:
            logger.exception("Error during agent loop iteration")
            final_content = f"Sorry, I encountered an error during execution: {e}"
            all_msgs = initial_messages

        self._save_turn(session, all_msgs, 1 + len(history))
        await self.sessions.save_async(session)

        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            metadata=msg.metadata or {},
        )

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        from datetime import datetime

        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")

            if (
                role == "tool"
                and isinstance(content, str)
                and len(content) > self._TOOL_RESULT_MAX_CHARS
            ):
                entry["content"] = content[: self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
            elif role == "user":
                if isinstance(content, str) and ContextBuilder._RUNTIME_CONTEXT_TAG in content:
                    try:
                        _, clean = content.split("\n\n", 1)
                        entry["content"] = (
                            clean
                            if not clean.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)
                            else content
                        )
                    except ValueError:
                        pass

                if isinstance(content, list):
                    new_content = []
                    for c in content:
                        if c.get("type") == "image_url" and c.get("image_url", {}).get(
                            "url", ""
                        ).startswith("data:image/"):
                            new_content.append({"type": "text", "text": "[image]"})
                        elif c.get(
                            "type"
                        ) == "text" and ContextBuilder._RUNTIME_CONTEXT_TAG in c.get("text", ""):
                            # Strip runtime context from text chunk
                            text = c.get("text", "")
                            parts = text.split("\n\n", 1)
                            if len(parts) == 2 and parts[0].startswith(
                                ContextBuilder._RUNTIME_CONTEXT_TAG
                            ):
                                new_content.append({"type": "text", "text": parts[1]})
                            else:
                                new_content.append(c)
                        else:
                            new_content.append(c)
                    entry["content"] = [c for c in new_content if c]

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

    async def _consolidate_memory(self, session, archive_all: bool = False) -> bool:
        """Delegate to MemoryStore.consolidate(). Returns True on success."""
        return await MemoryStore(self.workspace).consolidate(
            session,
            self.provider,
            self.model,
            archive_all=archive_all,
            memory_window=self.memory_window,
        )

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """Process a message directly (for CLI or cron usage)."""
        await self._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)

        lock = self._processing_locks.setdefault(session_key, asyncio.Lock())
        async with lock:
            response = await self._process_message(
                msg, session_key=session_key, on_progress=on_progress
            )
            return response.content if response else ""
