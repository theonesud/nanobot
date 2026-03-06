import asyncio
import os
import re
import subprocess
from collections import deque

from loguru import logger

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import OutboundMessage
from nanobot.utils.files import atomic_write


async def _commit(path, msg, ws):
    try:
        e = {
            **os.environ,
            "GIT_AUTHOR_NAME": "nanobot",
            "GIT_AUTHOR_EMAIL": "nanobot@ai",
            "GIT_COMMITTER_NAME": "nanobot",
            "GIT_COMMITTER_EMAIL": "nanobot@ai",
        }
        proc = await asyncio.create_subprocess_exec(
            "git", "add", str(path),
            cwd=str(ws),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        proc = await asyncio.create_subprocess_exec(
            "git", "commit", "-m", msg,
            cwd=str(ws),
            env=e,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
    except Exception:
        logger.debug("Auto-commit failed for {}", path, exc_info=True)


async def summarize_git_diffs(agent: AgentLoop, channel: str = "cli", chat_id: str = "direct"):
    try:
        p = await asyncio.create_subprocess_exec(
            "git",
            "log",
            "--since=24 hours ago",
            "-p",
            "--no-merges",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(agent.workspace),
        )
        o, e = await p.communicate()
        if p.returncode != 0 or not o.strip():
            return
        diff_text = o.decode(errors="replace")[:40000]
        res = await agent.process_direct(
            f"Summarize these git diffs:\n\n```diff\n{diff_text}\n```",
            session_key="bg:git",
            channel=channel,
            chat_id=chat_id,
        )
        if res:
            await agent.bus.publish_outbound(
                OutboundMessage(channel, chat_id, f"📊 **Git Summary**\n\n{res}")
            )
    except Exception:
        logger.exception("Git summary failed")


async def nightly_soul_update(agent: AgentLoop):
    h_f, s_f = agent.workspace / "memory/HISTORY.md", agent.workspace / "SOUL.md"
    if not h_f.exists():
        return
    try:
        with open(h_f, "r") as f:
            logs = list(deque(f, maxlen=500))
        res = await agent.process_direct(
            f"Update SOUL.md based on logs. Return ONLY content.\n\nSoul:\n{s_f.read_text() if s_f.exists() else ''}\n\nLogs:\n{''.join(logs)}",
            session_key="bg:soul",
            channel="system",
            chat_id="soul",
        )
        if res:
            c = res.strip()
            if c.startswith("```"):
                c = re.sub(r"^```[a-zA-Z]*\n", "", c)
                c = re.sub(r"\n```$", "", c)
            atomic_write(s_f, c)
            await _commit(s_f, "nanobot: nightly soul update", agent.workspace)
    except Exception:
        logger.exception("Soul update failed")


async def nightly_self_optimization(agent: AgentLoop):
    h_f = agent.workspace / "memory/HISTORY.md"
    if not h_f.exists():
        return
    try:
        with open(h_f, "r") as f:
            logs = "".join(deque(f, maxlen=1000))
        await agent.process_direct(
            f"Optimize yourself based on these logs. Create tools/rules as needed.\n\nLogs:\n{logs}",
            session_key="bg:opt",
            channel="system",
            chat_id="opt",
        )
    except Exception:
        logger.exception("Self-opt failed")
