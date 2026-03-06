import ast
import asyncio
import json
import os
import re
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path
from typing import Callable

import httpx
from loguru import logger
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from nanobot.cron.types import CronSchedule
from nanobot.utils.files import atomic_write as _atomic_write

_MAX_READ_SIZE = 10_000_000


def _resolve(path: str, ws: Path | None) -> Path:
    p = Path(path).expanduser()
    resolved = (ws / p).resolve() if not p.is_absolute() and ws else p.resolve()
    if ws and not str(resolved).startswith(str(ws.resolve())):
        raise ValueError(f"Path '{path}' escapes workspace boundary")
    return resolved


def _write(path: Path, content: str):
    _atomic_write(path, content)
    try:
        root = (
            subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(path.parent),
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
        e = {
            **os.environ,
            "GIT_AUTHOR_NAME": "nanobot",
            "GIT_AUTHOR_EMAIL": "nanobot@ai",
            "GIT_COMMITTER_NAME": "nanobot",
            "GIT_COMMITTER_EMAIL": "nanobot@ai",
        }
        subprocess.run(["git", "add", str(path)], cwd=root, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"nanobot: edit {path.name}"],
            cwd=root,
            env=e,
            check=True,
            capture_output=True,
        )
    except Exception:
        logger.debug("Auto-commit failed for {}", path, exc_info=True)


class ToolRegistry:
    def __init__(self, ws: Path):
        self.ws, self.tools = ws, {}

    def add(self, name: str, desc: str, params: dict, fn: Callable):
        self.tools[name] = {"name": name, "description": desc, "parameters": params, "fn": fn}

    def get_definitions(self):
        return [
            {
                "type": "function",
                "function": {k: v for k, v in t.items() if k != "fn"},
            }
            for t in self.tools.values()
        ]

    async def call(self, name: str, args: dict, **kwargs) -> str:
        if name not in self.tools:
            return f"Error: Tool {name} not found"
        try:
            fn = self.tools[name]["fn"]
            merged = {**args, **kwargs}
            if asyncio.iscoroutinefunction(fn):
                return await fn(**merged)
            return fn(**merged)
        except Exception as e:
            return f"Error: {e}"


async def connect_mcp(mcp_config, reg, stack):
    for name, srv in mcp_config.items():
        try:
            p = StdioServerParameters(command=srv.command, args=srv.args, env=srv.env)
            r, w = await stack.enter_async_context(stdio_client(p))
            s = await stack.enter_async_context(ClientSession(r, w))
            await s.initialize()
            res = await s.list_tools()
            for t in res.tools:
                t_n = f"{name}_{t.name}"

                async def m_fn(t_n=t_n, s=s, tool=t, **k):
                    r_c = await s.call_tool(tool.name, k)
                    return "\n".join(
                        [b.text if hasattr(b, "text") else str(b) for b in r_c.content]
                    )

                reg.add(t_n, t.description, t.inputSchema, m_fn)
        except Exception:
            logger.warning("Failed to connect MCP server '{}'", name, exc_info=True)


def register_builtin_tools(reg, ws: Path):
    async def read_file(path, **k):
        p = _resolve(path, ws)
        if not p.is_file():
            return f"Error: {path} not found"
        if p.stat().st_size > _MAX_READ_SIZE:
            return f"Error: File too large ({p.stat().st_size} bytes, limit {_MAX_READ_SIZE})"
        return p.read_text()

    reg.add(
        "read_file",
        "Read file",
        {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        read_file,
    )

    async def write_file(path, content, **k):
        p = _resolve(path, ws)
        _write(p, content)
        return f"Wrote to {path}"

    reg.add(
        "write_file",
        "Write file",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
        write_file,
    )

    async def edit_file(path, old_text, new_text, **k):
        p = _resolve(path, ws)
        s = p.read_text()
        if old_text not in s:
            return f"Error: old_text not found in {path}"
        _write(p, s.replace(old_text, new_text, 1))
        return f"Edited {path}"

    reg.add(
        "edit_file",
        "Edit file",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
        },
        edit_file,
    )

    async def list_dir(path, **k):
        p = _resolve(path, ws)
        return (
            "\n".join([("📁 " if i.is_dir() else "📄 ") + i.name for i in sorted(p.iterdir())])
            if p.is_dir()
            else f"Error: {path} not a directory"
        )

    reg.add(
        "list_dir",
        "List dir",
        {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        list_dir,
    )

    async def run_shell(command, use_docker=False, **k):
        env = {**os.environ}
        if k.get("_ctx_path_append"):
            env["PATH"] = f"{env.get('PATH', '')}:{k['_ctx_path_append']}"
        cmd = (
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{ws}:/workspace",
                "-w",
                "/workspace",
                "python:3.12-slim",
                "sh",
                "-c",
                command,
            ]
            if use_docker
            else ["sh", "-c", command]
        )
        p = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(ws),
            env=env,
        )
        o, e = await p.communicate()
        return (o.decode(errors="replace") + e.decode(errors="replace")) or "Success"

    reg.add(
        "exec",
        "Run shell",
        {
            "type": "object",
            "properties": {"command": {"type": "string"}, "use_docker": {"type": "boolean"}},
            "required": ["command"],
        },
        run_shell,
    )

    async def web(url, **k):
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as c:
            r = await c.get(url, headers={"User-Agent": "Nanobot/1.0"})
            t = re.sub(
                r"<(script|style|nav|footer|header).*?>.*?</\1>", "", r.text, flags=re.S | re.I
            )
            return f"URL: {url}\nContent: {re.sub(r'<.*?>', ' ', t)[:30000]}"

    reg.add(
        "web_fetch",
        "Web fetch",
        {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        web,
    )

    async def spawn(task, **k):
        depth = k.get("_ctx_session_key", "").count("sub:")
        if depth >= 2:
            return "Error: Depth limit reached."
        from .loop import AgentLoop

        parent = k.get("_ctx_loop")
        loop = AgentLoop(
            k["_ctx_bus"],
            k["_ctx_provider"],
            ws,
            config=parent.config if parent else None,
            mcp_config=parent.mcp_config if parent else None,
            cron_service=parent.cron if parent else None,
        )
        tid = f"sub:{uuid.uuid4().hex[:8]}"

        async def _run_subagent():
            try:
                await loop.process_direct(
                    task, session_key=f"{k.get('_ctx_session_key', 'main')}:{tid}"
                )
            except Exception:
                logger.exception("Subagent {} failed", tid)
            finally:
                await loop.close_mcp()

        asyncio.create_task(_run_subagent())
        return f"Started {tid}"

    reg.add(
        "spawn_agent",
        "Spawn subagent",
        {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]},
        spawn,
    )

    async def manage_tasks(action, id=None, title=None, status=None, **k):
        d = ws / "tasks"
        d.mkdir(parents=True, exist_ok=True)
        if action == "list":
            return "\n".join([f.read_text() for f in d.glob("*.json")])
        if action == "create":
            tid = id or uuid.uuid4().hex[:8]
            (d / f"{tid}.json").write_text(
                json.dumps({"id": tid, "title": title, "status": "todo"})
            )
            return f"Created {tid}"
        if action == "update" and id:
            p = d / f"{id}.json"
            if not p.exists():
                return f"Error: Task {id} not found"
            t = json.loads(p.read_text())
            t["title"], t["status"] = title or t["title"], status or t["status"]
            p.write_text(json.dumps(t))
            return f"Updated {id}"
        return "Error"

    reg.add(
        "manage_tasks",
        "Manage tasks",
        {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "create", "update"]},
                "id": {"type": "string"},
                "title": {"type": "string"},
                "status": {"type": "string"},
            },
            "required": ["action"],
        },
        manage_tasks,
    )

    async def add_job(name, schedule_kind, schedule_expr, message, **k):
        if not k.get("_ctx_cron"):
            return "Error: Cron service not available."
        every_ms = None
        if schedule_kind == "every":
            try:
                every_ms = int(schedule_expr)
            except ValueError:
                return f"Error: schedule_expr must be an integer (ms) for 'every' kind, got '{schedule_expr}'"
        sched = CronSchedule(
            kind=schedule_kind,
            expr=schedule_expr if schedule_kind == "cron" else None,
            every_ms=every_ms,
        )
        await k["_ctx_cron"].add_job(
            name, sched, message, channel=k["_ctx_msg"].channel, to=k["_ctx_msg"].chat_id
        )
        return f"Scheduled job: {name}"

    reg.add(
        "add_job",
        "Schedule a recurring task",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "schedule_kind": {"type": "string", "enum": ["every", "cron"]},
                "schedule_expr": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["name", "schedule_kind", "schedule_expr", "message"],
        },
        add_job,
    )

    async def send_message(content, **k):
        from nanobot.bus.events import OutboundMessage

        await k["_ctx_bus"].publish_outbound(
            OutboundMessage(k["_ctx_msg"].channel, k["_ctx_msg"].chat_id, content)
        )
        return "Sent."

    reg.add(
        "send_message",
        "Send message",
        {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]},
        send_message,
    )

    async def rewrite_code(path, symbol, new_code, **k):
        p = _resolve(path, ws)
        if not p.suffix == ".py":
            return f"Error: rewrite_code only supports Python files, got {p.suffix}"
        s = p.read_text()
        tree = ast.parse(s)

        def find_node(nodes, parts):
            if not parts:
                return None
            for n in nodes:
                if getattr(n, "name", None) == parts[0]:
                    if len(parts) == 1:
                        return n
                    if isinstance(n, ast.ClassDef):
                        return find_node(
                            [
                                sub
                                for sub in n.body
                                if isinstance(
                                    sub, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                                )
                            ],
                            parts[1:],
                        )
            return None

        target = find_node(
            [
                n
                for n in tree.body
                if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            ],
            symbol.split("."),
        )
        if not target:
            return f"Error: {symbol} not found in {path}"

        lines = s.splitlines(keepends=True)
        indent = re.match(r"^\s*", lines[target.lineno - 1]).group(0)
        code = re.compile(r"^", re.M).sub(indent, textwrap.dedent(new_code).strip()) + "\n"
        lines[target.lineno - 1 : target.end_lineno] = [code]
        _write(p, "".join(lines))
        if k.get("_ctx_loop") and hasattr(k["_ctx_loop"], "skills"):
            k["_ctx_loop"].skills.clear_cache()
        return f"Rewrote {symbol}"

    reg.add(
        "rewrite_code",
        "Rewrite Python code symbol",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "symbol": {"type": "string"},
                "new_code": {"type": "string"},
            },
            "required": ["path", "symbol", "new_code"],
        },
        rewrite_code,
    )

    async def rollback(commit="HEAD~1", **k):
        subprocess.run(["git", "reset", "--hard", commit], cwd=str(ws), check=True)
        return f"Reset to {commit}"

    reg.add(
        "rollback",
        "Git reset",
        {"type": "object", "properties": {"commit": {"type": "string"}}},
        rollback,
    )

    async def reload(**k):
        loop = k.get("_ctx_loop")
        if loop:
            await loop.close_mcp()
            loop.stop()
        os.execv(sys.executable, [sys.executable, "-m", "nanobot.cli.commands", "gateway"])

    reg.add("reload_nanobot", "Reload", {"type": "object", "properties": {}}, reload)
