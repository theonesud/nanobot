"""Shell execution tool."""

import asyncio
import os
import re
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool


class ExecTool(Tool):
    """Tool to execute shell commands."""

    def __init__(
        self,
        working_dir: str | None = None,
        timeout: int = 60,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
        path_append: str = "",
        bus: Any | None = None,
        auditor: Any | None = None,
    ):
        self.timeout = timeout
        self.working_dir = working_dir
        self.deny_patterns = deny_patterns or [
            r"rm\s+-[rf]{1,2}",  # rm -r, rm -rf, rm -fr
            r"del\s+/[fq]",  # del /f, del /q
            r"rmdir\s+/s",  # rmdir /s
            r"(?:^|[;&|]\s*)format",  # format (as standalone command only)
            r"(mkfs|diskpart)",  # disk operations
            r"dd\s+if=",  # dd
            r">\s*/dev/sd",  # write to disk
            r"(shutdown|reboot|poweroff)",  # system power
            r":\(\)\s*\{.*\};\s*:",  # fork bomb
            r"sudo",  # sudo
        ]
        self.allow_patterns = allow_patterns or []
        self.restrict_to_workspace = restrict_to_workspace
        self.path_append = path_append
        self.bus = bus
        self.auditor = auditor

    @property
    def name(self) -> str:
        return "exec"

    @property
    def description(self) -> str:
        return "Execute a shell command and return its output. Use with caution."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory for the command",
                },
            },
            "required": ["command"],
        }

    async def execute(self, command: str, working_dir: str | None = None, **kwargs: Any) -> str:
        cwd = working_dir or self.working_dir or os.getcwd()
        guard_error = self._guard_command(command, cwd)
        if guard_error:
            return guard_error

        # Phase 2: OpenCode Auditor check
        if self.auditor:
            from loguru import logger
            import uuid

            logger.info(f"Auditing command: {command}")
            verdict = await self.auditor.evaluate(command)
            if verdict == "UNSAFE":
                logger.warning(f"Auditor flagged command as UNSAFE: {command}")
                if self.bus:
                    from nanobot.bus.events import ApprovalRequest

                    request_id = str(uuid.uuid4())

                    # Notify user we are waiting for approval if possible
                    if factory := kwargs.get("outbound_msg_factory"):
                        await self.bus.publish_outbound(
                            factory(
                                content=f"⚠️ Auditor flagged this command as potentially unsafe. Please approve or reject:\n`{command}`"
                            )
                        )

                    req = ApprovalRequest(
                        id=request_id,
                        channel=kwargs.get("channel", "cli"),
                        chat_id=kwargs.get("chat_id"),
                        type="shell",
                        title="Run potentially unsafe command?",
                        content=command,
                        metadata={
                            "slack": {
                                "thread_ts": kwargs.get("metadata", {})
                                .get("slack", {})
                                .get("thread_ts")
                            }
                        },
                    )
                    await self.bus.publish_approval_request(req)

                    # Wait for approval
                    response = await self.bus.wait_for_approval(request_id)
                    if response and response.approved:
                        logger.info("Command approved by user.")
                        return await self._execute_safe(command, working_dir)
                    return f"Error: Command rejected by user or timed out: {response.reason if response else 'timeout'}"
                else:
                    return f"Error: Command blocked by Auditor (UNSAFE) and no interactive channel available for approval (channel: {kwargs.get('channel')})."

        return await self._execute_safe(command, working_dir)

    async def _execute_safe(self, command: str, working_dir: str | None) -> str:
        cwd = working_dir or self.working_dir or os.getcwd()
        env = os.environ.copy()
        if self.path_append:
            env["PATH"] = env.get("PATH", "") + os.pathsep + self.path_append

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )

            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
            except asyncio.TimeoutError:
                process.kill()
                # Wait for the process to fully terminate so pipes are
                # drained and file descriptors are released.
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                return f"Error: Command timed out after {self.timeout} seconds"

            output_parts = []

            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))

            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    output_parts.append(f"STDERR:\n{stderr_text}")

            if process.returncode != 0:
                output_parts.append(f"\nExit code: {process.returncode}")

            result = "\n".join(output_parts) if output_parts else "(no output)"

            # Truncate very long output
            max_len = 10000
            if len(result) > max_len:
                result = result[:max_len] + f"\n... (truncated, {len(result) - max_len} more chars)"

            return result

        except Exception as e:
            return f"Error executing command: {str(e)}"

    def _guard_command(self, command: str, cwd: str) -> str | None:
        """Best-effort safety guard for potentially destructive commands."""
        cmd = command.strip()
        # Fixed #20: Match as full words to allow 'mysudo.sh'
        lower = cmd.lower()
        for pattern in self.deny_patterns:
            if re.search(rf"\b{pattern}\b", lower):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

        if self.allow_patterns:
            if not any(re.search(p, lower) for p in self.allow_patterns):
                return "Error: Command blocked by safety guard (not in allowlist)"

        if self.restrict_to_workspace:
            if "..\\" in cmd or "../" in cmd:
                return "Error: Command blocked by safety guard (path traversal detected)"

            cwd_path = Path(cwd).resolve()

            win_paths = re.findall(r"[A-Za-z]:\\[^\\\"']+", cmd)
            # Only match absolute paths — avoid false positives on relative
            # paths like ".venv/bin/python" where "/bin/python" would be
            # incorrectly extracted by the old pattern.
            posix_paths = re.findall(r"(?:^|[\s|>])(/[^\s\"'>]+)", cmd)

            for raw in win_paths + posix_paths:
                try:
                    p = Path(raw.strip()).resolve()
                except Exception:
                    continue
                if p.is_absolute() and cwd_path not in p.parents and p != cwd_path:
                    return "Error: Command blocked by safety guard (path outside working dir)"

        return None
