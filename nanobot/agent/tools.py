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
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from nanobot.cron.types import CronSchedule
from nanobot.utils.files import atomic_write as _atomic_write


def _resolve(path: str, ws: Path | None) -> Path:
    p = Path(path).expanduser()
    return (ws / p).resolve() if not p.is_absolute() and ws else p.resolve()


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
        pass


class ToolRegistry:
    def __init__(self, ws: Path):
        self.ws, self.tools = ws, {}

    def add(self, name: str, desc: str, params: dict, fn: Callable):
        self.tools[name] = {"name": name, "description": desc, "parameters": params, "fn": fn}

    def get_definitions(self):
        return [{"type": "function", "function": t} for t in self.tools.values()]

    async def call(self, name: str, args: dict, **kwargs) -> str:
        if name not in self.tools:
            return f"Error: Tool {name} not found"
        try:
            fn = self.tools[name]["fn"]
            if asyncio.iscoroutinefunction(fn):
                return await fn(**{**kwargs, **args})
            return fn(**{**kwargs, **args})
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
            pass


def register_builtin_tools(reg, ws: Path):
    async def read_file(path, **k):
        p = _resolve(path, ws)
        return p.read_text() if p.is_file() else f"Error: {path} not found"

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

    async def exec(command, use_docker=False, **k):
        env = {**os.environ}
        if k.get("path_append"):
            env["PATH"] = f"{env.get('PATH', '')}:{k['path_append']}"
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
        return (o.decode() + e.decode()) or "Success"

    reg.add(
        "exec",
        "Run shell",
        {
            "type": "object",
            "properties": {"command": {"type": "string"}, "use_docker": {"type": "boolean"}},
            "required": ["command"],
        },
        exec,
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
        depth = k.get("session_key", "").count("sub:")
        if depth >= 2:
            return "Error: Depth limit reached."
        from .loop import AgentLoop

        loop = AgentLoop(k["bus"], k["provider"], ws)
        tid = f"sub:{uuid.uuid4().hex[:8]}"
        asyncio.create_task(
            loop.process_direct(task, session_key=f"{k.get('session_key', 'main')}:{tid}")
        )
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
        if not k.get("cron"):
            return "Error: Cron service not available."
        sched = CronSchedule(
            kind=schedule_kind,
            expr=schedule_expr if schedule_kind == "cron" else None,
            every_ms=int(schedule_expr) if schedule_kind == "every" else None,
        )
        await k["cron"].add_job(name, sched, message, channel=k["msg"].channel, to=k["msg"].chat_id)
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

        await k["bus"].publish_outbound(
            OutboundMessage(k["msg"].channel, k["msg"].chat_id, content)
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
        s = p.read_text()
        tree = ast.parse(s)
        lines = s.splitlines(keepends=True)
        nodes = [
            n
            for n in tree.body
            if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        target = None
        for i, part in enumerate(symbol.split(".")):
            m = next((n for n in nodes if getattr(n, "name", None) == part), None)
            if not m:
                return "Error: Not found"
            if i == len(symbol.split(".")) - 1:
                target = m
            elif isinstance(m, ast.ClassDef):
                nodes = [
                    n
                    for n in m.body
                    if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                ]
        pat = re.compile(r"^", re.M)
        indent = re.match(r"^\s*", lines[target.lineno - 1]).group(0)
        code = pat.sub(indent, textwrap.dedent(new_code).strip()) + "\n"
        lines[target.lineno - 1 : target.end_lineno] = [code]
        _write(p, "".join(lines))
        return f"Rewrote {symbol}"

    reg.add(
        "rewrite_code",
        "Rewrite code",
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
        if k.get("loop"):
            await k["loop"].close_mcp()
        os.execv(sys.executable, [sys.executable, "-m", "nanobot.cli.commands", "gateway"])

    reg.add("reload_nanobot", "Reload", {"type": "object", "properties": {}}, reload)
