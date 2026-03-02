import asyncio
import re
import subprocess
from collections import deque

from loguru import logger

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import OutboundMessage


async def summarize_git_diffs(agent: AgentLoop, channel: str = "slack", chat_id: str = "general"):
    logger.info("Heartbeat: starting git diff summary task")
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "log",
            "--since='24 hours ago'",
            "-p",
            "--no-merges",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(agent.workspace),
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error("Git diff failed: {}", stderr.decode())
            return
        if not stdout.decode().strip():
            logger.info("No git diffs found for the last 24 hours.")
            return
        response = await agent.process_direct(
            f"Review the following git diffs from the last 24 hours and provide a concise summary of the changes, grouped by project or theme. Highlight any critical updates or potential issues.\n\n```diff\n{stdout.decode()[:50000]}\n```",
            session_key="background:git_summary",
            channel=channel,
            chat_id=chat_id,
        )
        if response:
            await agent.bus.publish_outbound(
                OutboundMessage(
                    channel=channel,
                    chat_id=chat_id,
                    content=f"📊 **Daily Git Activity Summary**\n\n{response}",
                )
            )
    except Exception:
        logger.exception("Failed to summarize git diffs")


async def nightly_soul_update(agent: AgentLoop):
    logger.info("Heartbeat: starting nightly soul update task")
    history_file = agent.workspace / "memory" / "HISTORY.md"
    soul_file = agent.workspace / "SOUL.md"
    if not history_file.exists():
        logger.warning("HISTORY.md not found, skipping soul update")
        return
    try:
        with open(history_file, "r", encoding="utf-8") as f:
            all_logs = list(deque(f, maxlen=500))
        updated_soul = await agent.process_direct(
            f"Review the following daily activity logs and your current core persona (SOUL.md). Identify any new preferences, recurring topics, or important decisions made by the user today. Provide an UPDATED version of SOUL.md that incorporates these new insights while preserving its core structure and existing knowledge. ONLY respond with the markdown content of the updated SOUL.md.\n\n## Current SOUL.md\n{(soul_file.read_text(encoding='utf-8') if soul_file.exists() else '')}\n\n## Daily Logs\n{''.join(all_logs)}",
            session_key="background:soul_update",
            channel="system",
            chat_id="soul_update",
        )
        if updated_soul and ("---" in updated_soul or "# " in updated_soul):
            content = updated_soul.strip()
            if content.startswith("```"):
                content = re.sub(r"^```[a-zA-Z]*\n", "", content)
                content = re.sub(r"\n```$", "", content)
            soul_file.write_text(content.strip(), encoding="utf-8")
            from nanobot.agent.tools.filesystem import _git_commit

            _git_commit(soul_file, "nanobot: nightly soul update")
            logger.info("✓ SOUL.md updated with daily insights")
    except Exception:
        logger.exception("Failed to update SOUL.md")


async def nightly_self_optimization(agent: AgentLoop):
    logger.info("Heartbeat: starting nightly self-optimization session")
    history_file = agent.workspace / "memory" / "HISTORY.md"
    if not history_file.exists():
        return
    try:
        with open(history_file, "r", encoding="utf-8") as f:
            logs = [line.strip() for line in deque(f, maxlen=1000)]
        planning_prompt = (
            "Review your recent activity logs and current codebase. Identify one specific way you can improve yourself today. "
            "This could be: (1) Creating a new skill/tool for a recurring task, (2) Refactoring a clunky piece of your own code, "
            "(3) Updating a policy/rule in MEMORY.md. Plan and then EXECUTE the improvement using your tools. "
            "If no improvement is needed, say 'All systems optimal'.\n\nLogs:\n" + "\n".join(logs)
        )
        await agent.process_direct(
            planning_prompt,
            session_key="background:optimization",
            channel="system",
            chat_id="self_opt",
        )
        logger.info("✓ Nightly self-optimization completed")
    except Exception:
        logger.exception("Failed nightly self-optimization")
