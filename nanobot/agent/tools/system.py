import asyncio
import os
import sys
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool


class ReloadTool(Tool):
    name = "reload_agent"
    description = (
        "Restart the current agent process to apply code changes or configuration updates."
    )
    parameters = {
        "type": "object",
        "properties": {"reason": {"type": "string", "description": "Reason for reloading."}},
        "required": ["reason"],
    }

    async def execute(self, **kwargs: Any) -> str:
        reason = kwargs.get("reason", "No reason provided")
        logger.warning("🔄 RELOADING AGENT: {}", reason)

        async def _delayed_reload():
            await asyncio.sleep(2)
            executable = sys.executable
            args = sys.argv[:]
            logger.info("Execv: {} {}", executable, args)
            os.execv(executable, [executable] + args)

        asyncio.create_task(_delayed_reload())
        return "🔄 Reload initiated. I will be back in a few seconds."
