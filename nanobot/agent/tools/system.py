"""System tools for agent self-management."""

import os
import sys
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool


class ReloadTool(Tool):
    """
    Restart the agent process.
    Use this after modifying source code to apply changes.
    """

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

        # Give a small delay to allow the response to be published to the bus
        # and delivered to the user before we kill the process.
        asyncio = __import__("asyncio")  # Import here to avoid top-level issues if any

        async def _delayed_reload():
            await asyncio.sleep(2)
            executable = sys.executable
            args = sys.argv[:]
            logger.info("Execv: {} {}", executable, args)
            os.execv(executable, [executable] + args)

        # Trigger background task for reload
        asyncio.create_task(_delayed_reload())

        return "🔄 Reload initiated. I will be back in a few seconds."
