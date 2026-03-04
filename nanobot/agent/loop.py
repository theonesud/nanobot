import asyncio
import copy
import json
import platform
import uuid
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from nanobot.bus.events import ApprovalRequest, InboundMessage, OutboundMessage
from nanobot.session.manager import SessionManager
from nanobot.utils.database import Database
from nanobot.utils.helpers import get_model_pricing, strip_think

from .tools import ToolRegistry, connect_mcp, register_builtin_tools

_AUDITOR_KEYWORDS = [
    "exec", "write", "edit", "rewrite", "delete", "remove", "bash", "cmd", "rollback", "reset",
]


class SkillsLoader:
    def __init__(self, ws):
        self.ws, self._cache = ws, None

    def list_skills(self):
        if self._cache is not None:
            return self._cache
        s = []
        for d in [(self.ws / "skills"), (Path(__file__).parent.parent / "skills")]:
            if d.exists():
                for sd in d.iterdir():
                    if sd.is_dir() and (sd / "SKILL.md").exists():
                        s.append({"name": sd.name, "path": sd / "SKILL.md"})
        self._cache = s
        return s

    def clear_cache(self):
        self._cache = None


class AgentLoop:
    def __init__(
        self,
        bus,
        provider,
        workspace,
        cron_service=None,
        mcp_config=None,
        auto_mcp=True,
        b_dir=None,
        config=None,
        **k,
    ):
        self.config = config
        self.bus, self.provider, self.workspace, self.model = (
            bus,
            provider,
            workspace,
            provider.get_default_model(),
        )
        self.db, self.sessions, self.cron = (
            Database(workspace),
            SessionManager(workspace),
            cron_service,
        )
        self.bus.db = self.db
        self.mcp_config = mcp_config or (
            getattr(getattr(config, "tools", None), "mcp_servers", {}) if config else {}
        )
        self.path_append = k.get("path_append", "")

        if auto_mcp and "playwright" not in self.mcp_config:
            self.mcp_config["playwright"] = type(
                "srv",
                (),
                {
                    "command": "npx",
                    "args": ["-y", "@playwright/mcp", "headless"]
                    if not b_dir
                    else ["-y", "@playwright/mcp", "--user-data-dir", b_dir],
                    "env": None,
                },
            )
        self.tools, self._stack, self.skills = (
            ToolRegistry(workspace),
            AsyncExitStack(),
            SkillsLoader(workspace),
        )
        register_builtin_tools(self.tools, workspace)
        self._active, self._locks, self._running = {}, {}, False

    @property
    def _budget(self):
        return self.config.agents.defaults.daily_budget_usd if self.config else 5.0

    async def run(self):
        self._running = True
        try:
            await self._stack.__aenter__()
            if self.mcp_config:
                await connect_mcp(self.mcp_config, self.tools, self._stack)
            while self._running:
                try:
                    if self.bus.inbound.empty():
                        await asyncio.sleep(0.1)
                        continue
                    msg = self.bus.inbound.get_nowait()
                    _, _, msg = msg
                except asyncio.QueueEmpty:
                    continue
                if msg.content.strip().lower() == "/stop":
                    for t in self._active.pop(msg.session_key, []):
                        t.cancel()
                    await self.bus.publish_outbound(
                        OutboundMessage(msg.channel, msg.chat_id, "Stopped.")
                    )
                    continue
                t = asyncio.create_task(self._dispatch(msg))
                self._active.setdefault(msg.session_key, []).append(t)

                def _done_cb(_, k=msg.session_key, task=t):
                    lst = self._active.get(k)
                    if lst and task in lst:
                        lst.remove(task)

                t.add_done_callback(_done_cb)
        finally:
            await self._stack.aclose()

    async def _dispatch(self, msg):
        lock = self._locks.setdefault(msg.session_key, asyncio.Lock())
        async with lock:
            try:
                res = await self._process(msg)
                if res:
                    await self.bus.publish_outbound(res)
            except Exception as e:
                logger.exception("Error")
                self.db.log_trace(msg.session_key, "error", {"error": str(e), "msg": msg.content})
                await self.bus.publish_outbound(
                    OutboundMessage(msg.channel, msg.chat_id, f"Error: {e}")
                )
            finally:
                if not self._active.get(msg.session_key):
                    self._locks.pop(msg.session_key, None)

    async def _process(self, msg, session_key=None):
        sess = self.sessions.get_or_create(session_key or msg.session_key)
        content = msg.content
        if msg.metadata.get("is_godmode"):
            content += "\n\n[SYSTEM: GOD MODE] You may modify source code."
        history = sess.get_history(50)
        for h in history:
            if isinstance(h.get("content"), list):
                h["content"] = "".join(
                    [i.get("text", "") for i in h["content"] if i.get("type") == "text"]
                )

        now = datetime.now(timezone.utc)
        sys_prompt = f"Time: {now} | {platform.system()} | {self.workspace}\n"
        for f in ["IDENTITY.md", "SOUL.md", "USER.md", "AGENTS.md"]:
            fp = self.workspace / f
            if fp.exists():
                try:
                    sys_prompt += f"\n## {f}\n" + fp.read_text()
                except Exception:
                    logger.debug("Failed to read {}", fp)
        mem_fp = self.workspace / "memory/MEMORY.md"
        if mem_fp.exists():
            try:
                sys_prompt += "\n## Memory\n" + mem_fp.read_text()
            except Exception:
                logger.debug("Failed to read {}", mem_fp)
        if sk := self.skills.list_skills():
            sys_prompt += "\n## Skills\n" + "\n".join([f"- {s['name']}" for s in sk])
        if self.cron and (jobs := self.cron.list_jobs()):
            sys_prompt += "\n## Cron Jobs\n" + "\n".join([f"- {j.name}" for j in jobs])

        user_msg = [{"type": "text", "text": content}]
        for m in msg.media:
            user_msg.append({"type": "image_url", "image_url": {"url": m}})
        msgs = (
            [{"role": "system", "content": sys_prompt}]
            + history
            + [{"role": "user", "content": user_msg}]
        )

        iters, final = 0, ""
        max_iters = self.config.agents.defaults.max_tool_iterations if self.config else 40
        while iters < max_iters:
            iters += 1
            if self.db.get_daily_cost() > self._budget:
                for t in self._active.pop(msg.session_key, []):
                    t.cancel()
                return OutboundMessage(msg.channel, msg.chat_id, "Budget exceeded for this session.")
            self.db.log_trace(sess.key, "llm_request", {"model": self.model, "messages": msgs})
            resp = await self.provider.chat(
                messages=msgs, tools=self.tools.get_definitions(), model=self.model
            )
            self.db.log_trace(
                sess.key, "llm_response", resp.__dict__ if hasattr(resp, "__dict__") else str(resp)
            )
            if resp.error:
                return OutboundMessage(msg.channel, msg.chat_id, f"Provider Error: {resp.error}")
            if resp.usage:
                p, c = resp.usage.get("prompt_tokens", 0), resp.usage.get("completion_tokens", 0)
                ri, ro = get_model_pricing(self.model)
                self.db.log_cost(sess.key, "OpenCode", self.model, p, c, (p * ri + c * ro) / 1e6)

            msgs.append(
                {
                    "role": "assistant",
                    "content": resp.content,
                    **(
                        {
                            "tool_calls": [
                                {
                                    "id": t.id,
                                    "type": "function",
                                    "function": {
                                        "name": t.name,
                                        "arguments": json.dumps(t.arguments),
                                    },
                                }
                                for t in resp.tool_calls
                            ]
                        }
                        if resp.has_tool_calls
                        else {}
                    ),
                }
            )
            if not resp.has_tool_calls:
                final = strip_think(resp.content)
                break

            for tc in resp.tool_calls:
                await self.bus.publish_outbound(
                    OutboundMessage(
                        msg.channel, msg.chat_id, f"⚙️ {tc.name}", metadata={"_progress": True}
                    )
                )
                if tc.name not in self.tools.tools:
                    msgs.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": tc.name,
                            "content": f"Error: Tool {tc.name} not found",
                        }
                    )
                    continue

                if any(k in tc.name.lower() for k in _AUDITOR_KEYWORDS):
                    a_m = self.config.agents.defaults.auditor_model if self.config else self.model
                    aud_msgs = [
                        {
                            "role": "system",
                            "content": "Security check. SAFE or DANGEROUS: <reason>",
                        },
                        {"role": "user", "content": f"{tc.name}: {tc.arguments}"},
                    ]
                    self.db.log_trace(
                        sess.key, "auditor_request", {"model": a_m, "messages": aud_msgs}
                    )
                    aud_resp = await self.provider.chat(aud_msgs, model=a_m)
                    self.db.log_trace(
                        sess.key,
                        "auditor_response",
                        aud_resp.__dict__ if hasattr(aud_resp, "__dict__") else str(aud_resp),
                    )
                    if aud_resp.error:
                        logger.warning("Auditor failed, blocking tool call: {}", aud_resp.error)
                        aud = "DANGEROUS"
                    else:
                        aud = (aud_resp.content or "").upper()
                    if "DANGEROUS" in aud:
                        rid = str(uuid.uuid4())
                        await self.bus.publish_approval_request(
                            ApprovalRequest(
                                id=rid,
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                title=f"Audit: {tc.name}",
                                content=str(tc.arguments),
                            )
                        )
                        ans = await self.bus.wait_for_approval(rid)
                        if not ans or not ans.approved:
                            msgs.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc.id,
                                    "name": tc.name,
                                    "content": "Rejected.",
                                }
                            )
                            continue

                try:
                    self.db.log_trace(
                        sess.key, "tool_call", {"name": tc.name, "args": tc.arguments}
                    )
                    res = await asyncio.wait_for(
                        self.tools.call(
                            tc.name,
                            tc.arguments,
                            _ctx_bus=self.bus,
                            _ctx_msg=msg,
                            _ctx_provider=self.provider,
                            _ctx_cron=self.cron,
                            _ctx_session_key=sess.key,
                            _ctx_path_append=self.path_append,
                            _ctx_loop=self,
                        ),
                        60.0,
                    )
                    self.db.log_trace(
                        sess.key, "tool_result", {"name": tc.name, "result": str(res)}
                    )
                except asyncio.TimeoutError:
                    res = "Error: Tool timed out."
                    self.db.log_trace(sess.key, "tool_error", {"name": tc.name, "error": "timeout"})
                except Exception as e:
                    res = f"Error: {e}"
                    self.db.log_trace(sess.key, "tool_error", {"name": tc.name, "error": str(e)})

                tagged_res = f"<untrusted_context>\n{res}\n</untrusted_context>"
                msgs.append(
                    {"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": tagged_res}
                )
        else:
            final = "I reached the maximum number of tool iterations without a final answer."

        skip = len(history) + 2
        history_lines = []
        for m in msgs[skip:]:
            e = copy.deepcopy(m)
            e["timestamp"] = now.isoformat()
            sess.messages.append(e)
            history_lines.append(
                f"\n[{e.get('role')}]: {e.get('content', '')} {e.get('tool_calls', '')}\n"
            )

        if history_lines:
            p = self.workspace / "memory/HISTORY.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(p, "a") as f:
                    f.writelines(history_lines)
            except OSError:
                logger.debug("Failed to write HISTORY.md")

        if len(sess.messages) - sess.last_consolidated > 50:
            snapshot = copy.deepcopy(sess.messages)
            last_c = sess.last_consolidated
            asyncio.create_task(self._consolidate(sess, snapshot, last_c))
        await self.sessions.save_async(sess)
        return OutboundMessage(msg.channel, msg.chat_id, final or "")

    async def _consolidate(self, sess, messages_snapshot, last_consolidated):
        try:
            old = messages_snapshot[last_consolidated:-10]
            if not old:
                return
            p = "Summarize for memory:\n" + "\n".join(
                [f"[{m.get('role')}]: {m.get('content')}" for m in old]
            )
            con_msgs = [
                {"role": "system", "content": "Memory manager."},
                {"role": "user", "content": f"Update memory:\n{p}"},
            ]
            self.db.log_trace(
                sess.key, "consolidation_request", {"model": self.model, "messages": con_msgs}
            )
            r = await self.provider.chat(con_msgs, model=self.model)
            self.db.log_trace(
                sess.key, "consolidation_response", r.__dict__ if hasattr(r, "__dict__") else str(r)
            )
            with open(self.workspace / "memory/MEMORY.md", "a") as f:
                f.write(f"\n### {datetime.now(timezone.utc).date()}\n{r.content}\n")
            self.db.log_trace(sess.key, "memory_consolidation", {"summary": r.content})
            sess.last_consolidated = len(messages_snapshot) - 10
        except Exception:
            logger.exception("Memory consolidation failed")

    async def process_direct(
        self, content, session_key="cli:direct", channel="cli", chat_id="direct"
    ):
        r = await self._process(InboundMessage(channel, chat_id, content), session_key)
        return r.content if r else ""

    async def close_mcp(self):
        await self._stack.aclose()

    def stop(self):
        self._running = False
