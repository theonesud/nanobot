import asyncio
import copy
import json
import platform
import uuid
from contextlib import AsyncExitStack
from datetime import datetime
from pathlib import Path

from loguru import logger

from nanobot.bus.events import ApprovalRequest, InboundMessage, OutboundMessage
from nanobot.session.manager import SessionManager
from nanobot.utils.database import Database
from nanobot.utils.helpers import get_model_pricing, strip_think

from .tools import ToolRegistry, connect_mcp, register_builtin_tools


class SkillsLoader:
    def __init__(self, ws):
        self.ws = ws

    def list_skills(self):
        s = []
        for d in [(self.ws / "skills"), (Path(__file__).parent.parent / "skills")]:
            if d.exists():
                for sd in d.iterdir():
                    if sd.is_dir() and (sd / "SKILL.md").exists():
                        s.append({"name": sd.name, "path": sd / "SKILL.md"})
        return s

    def load_skill(self, name):
        for d in [(self.ws / "skills"), (Path(__file__).parent.parent / "skills")]:
            if (d / name / "SKILL.md").exists():
                return (d / name / "SKILL.md").read_text()
        return None


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
        self.db, self.sessions, self.cron, self.mcp_config = (
            Database(workspace),
            SessionManager(workspace),
            cron_service,
            mcp_config or {},
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

    async def run(self):
        self._running = True
        try:
            await self._stack.__aenter__()
            if self.mcp_config:
                await connect_mcp(self.mcp_config, self.tools, self._stack)
            while self._running:
                try:
                    msg = await asyncio.wait_for(self.bus.consume_inbound(), 1.0)
                except asyncio.TimeoutError:
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
                t.add_done_callback(
                    lambda _, k=msg.session_key, task=t: (
                        self._active[k].remove(task) if k in self._active else None
                    )
                )
        finally:
            await self._stack.aclose()

    async def _dispatch(self, msg):
        async with self._locks.setdefault(msg.session_key, asyncio.Lock()):
            try:
                res = await self._process(msg)
                if res:
                    await self.bus.publish_outbound(res)
            except Exception as e:
                logger.exception("Error")
                await self.bus.publish_outbound(
                    OutboundMessage(msg.channel, msg.chat_id, f"Error: {e}")
                )

    async def _process(self, msg, session_key=None):
        sess = self.sessions.get_or_create(session_key or msg.session_key)
        if msg.metadata.get("is_godmode"):
            msg.content += "\n\n[SYSTEM: GOD MODE] You may modify source code."
        history = sess.get_history(50)
        # Sanitize history (Sanitary Logic from baseline)
        for h in history:
            if isinstance(h.get("content"), list):
                h["content"] = "".join(
                    [i.get("text", "") for i in h["content"] if i.get("type") == "text"]
                )

        sys = f"Time: {datetime.now()} | {platform.system()} | {self.workspace}\n"
        for f in ["IDENTITY.md", "SOUL.md", "USER.md", "AGENTS.md"]:
            if (self.workspace / f).exists():
                sys += f"\n## {f}\n" + (self.workspace / f).read_text()
        if (self.workspace / "memory/MEMORY.md").exists():
            sys += "\n## Memory\n" + (self.workspace / "memory/MEMORY.md").read_text()
        if sk := self.skills.list_skills():
            sys += "\n## Skills\n" + "\n".join([f"- {s['name']}" for s in sk])
        if self.cron and (jobs := self.cron.list_jobs()):
            sys += "\n## Cron Jobs\n" + "\n".join([f"- {j.name}" for j in jobs])

        user_msg = [{"type": "text", "text": msg.content}]
        for m in msg.media:
            user_msg.append({"type": "image_url", "image_url": {"url": m}})
        msgs = (
            [{"role": "system", "content": sys}] + history + [{"role": "user", "content": user_msg}]
        )

        iters, final = 0, ""
        max_iters = self.config.agents.defaults.max_tool_iterations if self.config else 40
        while iters < max_iters:
            iters += 1
            if self.db.get_daily_cost() > 5.0:
                return OutboundMessage(msg.channel, msg.chat_id, "Budget exceeded.")
            resp = await self.provider.chat(
                messages=msgs, tools=self.tools.get_definitions(), model=self.model
            )
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
                if tc.name in {"exec", "write_file", "edit_file", "rewrite_code"}:
                    a_m = self.config.agents.defaults.auditor_model if self.config else self.model
                    aud_resp = await self.provider.chat(
                        [
                            {
                                "role": "system",
                                "content": "Security check. SAFE or DANGEROUS: <reason>",
                            },
                            {"role": "user", "content": f"{tc.name}: {tc.arguments}"},
                        ],
                        model=a_m,
                    )
                    aud = aud_resp.content.upper()
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
                    res = await asyncio.wait_for(
                        self.tools.call(
                            tc.name,
                            tc.arguments,
                            bus=self.bus,
                            msg=msg,
                            provider=self.provider,
                            cron=self.cron,
                            session_key=sess.key,
                            path_append=self.path_append,
                            loop=self,
                        ),
                        60.0,
                    )
                except asyncio.TimeoutError:
                    res = "Error: Tool timed out."

                tagged_res = f"<untrusted_context>\n{res}\n</untrusted_context>"
                msgs.append(
                    {"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": tagged_res}
                )

        skip = len(history) + 2
        for m in msgs[skip:]:
            e = copy.deepcopy(m)
            e["timestamp"] = datetime.now().isoformat()
            sess.messages.append(e)
            p = self.workspace / "memory/HISTORY.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a") as f:
                f.write(f"\n[{e.get('role')}]: {e.get('content', '')} {e.get('tool_calls', '')}\n")

        if len(sess.messages) - sess.last_consolidated > 50:
            asyncio.create_task(self._consolidate(sess))
        await self.sessions.save_async(sess)
        return OutboundMessage(msg.channel, msg.chat_id, final)

    async def _consolidate(self, sess):
        try:
            old = sess.messages[sess.last_consolidated : -10]
            if not old:
                return
            p = "Summarize for memory:\n" + "\n".join(
                [f"[{m.get('role')}]: {m.get('content')}" for m in old]
            )
            r = await self.provider.chat(
                [
                    {"role": "system", "content": "Memory manager."},
                    {"role": "user", "content": f"Update memory:\n{p}"},
                ],
                model=self.model,
            )
            with open(self.workspace / "memory/MEMORY.md", "a") as f:
                f.write(f"\n### {datetime.now().date()}\n{r.content}\n")
            sess.last_consolidated = len(sess.messages) - 10
        except Exception:
            pass

    async def process_direct(
        self, content, session_key="cli:direct", channel="cli", chat_id="direct"
    ):
        r = await self._process(InboundMessage(channel, "user", chat_id, content), session_key)
        return r.content if r else ""

    async def close_mcp(self):
        await self._stack.aclose()

    def stop(self):
        self._running = False
