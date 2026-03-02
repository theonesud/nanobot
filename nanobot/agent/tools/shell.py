import asyncio
import os
import re
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool
from nanobot.bus.events import ApprovalRequest


class ExecTool(Tool):
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
        use_docker: bool = False,
        docker_image: str | None = None,
    ):
        self.timeout = timeout
        self.working_dir = working_dir
        self.use_docker = use_docker
        self.docker_image = docker_image
        self.deny_patterns = deny_patterns or [
            "rm\\s+-[rf]{1,2}",
            "del\\s+/[fq]",
            "rmdir\\s+/s",
            "(?:^|[;&|]\\s*)format",
            "(mkfs|diskpart)",
            "dd\\s+if=",
            ">\\s*/dev/sd",
            "(shutdown|reboot|poweroff)",
            ":\\(\\)\\s*\\{.*\\};\\s*:",
            "sudo",
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
        if self.auditor:
            logger.info("Auditing command: {}", command)
            verdict = await self.auditor.evaluate(command)
            if verdict == "UNSAFE":
                logger.warning("Auditor flagged command as UNSAFE: {}", command)
                if self.bus:
                    request_id = str(uuid.uuid4())
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
                    response = await self.bus.wait_for_approval(request_id)
                    if response and response.approved:
                        logger.info("Command approved by user.")
                        return await self._execute_safe(command, working_dir)
                    return f"Error: Command rejected by user or timed out: {(response.reason if response else 'timeout')}"
                else:
                    return f"Error: Command blocked by Auditor (UNSAFE) and no interactive channel available for approval (channel: {kwargs.get('channel')})."
        return await self._execute_safe(command, working_dir)

    async def _execute_safe(self, command: str, working_dir: str | None) -> str:
        cwd = working_dir or self.working_dir or os.getcwd()
        if self.use_docker and self.docker_image:
            import shlex

            cmd_q = shlex.quote(command)
            cwd_q = shlex.quote(cwd)
            command = f"docker run --rm -v {cwd_q}:/workspace -w /workspace {self.docker_image} /bin/sh -c {cmd_q}"
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
            max_len = 10000
            if len(result) > max_len:
                result = result[:max_len] + f"\n... (truncated, {len(result) - max_len} more chars)"
            return result
        except Exception as e:
            return f"Error executing command: {str(e)}"

    def _guard_command(self, command: str, cwd: str) -> str | None:
        cmd = command.strip()
        lower = cmd.lower()
        for pattern in self.deny_patterns:
            if re.search(pattern, lower):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"
        if self.allow_patterns:
            if not any((re.search(p, lower) for p in self.allow_patterns)):
                return "Error: Command blocked by safety guard (not in allowlist)"
        if self.restrict_to_workspace:
            if "..\\" in cmd or "../" in cmd:
                return "Error: Command blocked by safety guard (path traversal detected)"
            cwd_path = Path(cwd).resolve()
            win_paths = re.findall("[A-Za-z]:\\\\[^\\\\\\\"']+", cmd)
            posix_paths = re.findall("(?:^|[\\s|>])(/[^\\s\\\"'>]+)", cmd)
            for raw in win_paths + posix_paths:
                try:
                    p = Path(raw.strip()).resolve()
                except Exception:
                    continue
                if p.is_absolute() and cwd_path not in p.parents and (p != cwd_path):
                    return "Error: Command blocked by safety guard (path outside working dir)"
        return None
