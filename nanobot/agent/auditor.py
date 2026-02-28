"""Auditor for security evaluation of shell commands."""

import asyncio
import json
from typing import Literal

from loguru import logger


class CommandAuditor:
    """
    The Auditor is a secondary OpenCode subprocess that evaluates
    shell commands for destructive actions.
    """

    def __init__(self, bin_path: str = "opencode"):
        self.bin_path = bin_path
        self.system_prompt = (
            "You are a strict security auditor. Your ONLY job is to evaluate if a shell command "
            "is SAFE or UNSAFE to execute on the host system. "
            "Deny any command that uses 'rm -rf', 'sudo', 'mkfs', 'dd', or attempts to access secrets/keys. "
            "Reply with exactly one word: SAFE or UNSAFE."
        )

    async def evaluate(self, command: str) -> Literal["SAFE", "UNSAFE"]:
        """
        Evaluate a command using a secondary OpenCode instance.
        """
        prompt = f"Evaluate this command for destructive actions: {command}\nReply SAFE or UNSAFE."

        args = [
            self.bin_path,
            "run",
            "--message",
            prompt,
            "--system-prompt",
            self.system_prompt,
            "--format",
            "json",
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            full_content = []

            async def consume_stderr():
                if process.stderr:
                    async for _ in process.stderr:
                        pass

            stderr_task = asyncio.create_task(consume_stderr())

            if process.stdout:
                async for line in process.stdout:
                    if not line:
                        break
                    try:
                        event = json.loads(line.decode().strip())
                        if event.get("type") == "text":
                            full_content.append(event["part"]["text"])
                    except Exception as e:
                        logger.debug(f"Auditor: failed to parse line: {e}")

            await process.wait()
            await stderr_task

            result = "".join(full_content).strip().upper()

            # Use strict matching for SAFE, default to UNSAFE if ambiguous
            if "SAFE" in result and "UNSAFE" not in result:
                return "SAFE"

            return "UNSAFE"

        except Exception:
            logger.exception("Auditor execution failed")
            return "UNSAFE"
