"""OpenCode CLI provider implementation."""

import asyncio
import json
from typing import Any, Awaitable, Callable

from loguru import logger

from nanobot.providers.base import LLMProvider, LLMResponse


class OpenCodeProvider(LLMProvider):
    """
    LLM provider using the OpenCode CLI (`opencode run`).

    This provider delegates reasoning and tool execution to the OpenCode CLI tool.
    By default, it uses the installed `opencode` binary.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        bin_path: str = "opencode",
        default_model: str = "opencode-default",
    ):
        super().__init__(api_key, api_base)
        self.bin_path = bin_path
        self.default_model = default_model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> LLMResponse:
        """
        Send a chat completion request via OpenCode CLI.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions.
            model: Model identifier.
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.
            on_progress: Optional callback to stream progress.

        Returns:
            LLMResponse with content.
        """
        # Build prompt from conversation history to provide full context
        prompt_parts = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content") or ""
            if role == "system":
                continue  # System prompt is passed via separate flag
            # Use XML tags which are better understood by models for turn-taking
            prompt_parts.append(f"<{role.upper()}>\n{content}\n</{role.upper()}>")

        system_msg = next((m.get("content") for m in messages if m.get("role") == "system"), None)

        full_prompt = "\n\n".join(prompt_parts)
        if system_msg:
            full_prompt = f"<SYSTEM>\n{system_msg}\n</SYSTEM>\n\n{full_prompt}"

        # We append a final instruction so the model knows it's its turn to reply
        full_prompt += "\n\n<SYSTEM>\nPlease respond to the last <USER> message.</SYSTEM>"

        if tools:
            # Fixed #11: Inform OpenCode about available tools in the system context
            tool_summary = "\n".join(
                [
                    f"- {t['function']['name']}: {t['function'].get('description', '')}"
                    for t in tools
                ]
            )
            full_prompt = (
                f"<SYSTEM>\nYou can use these Nanobot tools if needed:\n{tool_summary}\n</SYSTEM>\n\n"
                + full_prompt
            )

        args = [self.bin_path, "run", "--message", full_prompt, "--format", "json"]

        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            full_content = []
            usage = {}
            finish_reason = "stop"
            step_count = 0
            stderr_buffer = []

            async def consume_stderr():
                if process.stderr:
                    async for line in process.stderr:
                        if line:
                            stderr_buffer.append(line.decode(errors="replace"))

            stderr_task = asyncio.create_task(consume_stderr())

            if process.stdout:
                async for line in process.stdout:
                    if not line:
                        break
                    try:
                        event = json.loads(line.decode().strip())
                        evt_type = event.get("type")
                        part = event.get("part", {})

                        if evt_type == "step_start":
                            step_count += 1
                            if on_progress:
                                await on_progress(f"⚙️ opencode: Thinking (Step {step_count})...")
                        elif evt_type == "text":
                            full_content.append(part.get("text", ""))
                        elif evt_type == "tool_use":
                            if on_progress:
                                tool_name = part.get("tool", "tool")
                                state = part.get("state", {})
                                target = state.get("title") or state.get("command") or ""
                                if len(target) > 50:
                                    target = target[:50] + "..."
                                msg = (
                                    f"⚙️ opencode: {tool_name}({target})"
                                    if target
                                    else f"⚙️ opencode: {tool_name}"
                                )
                                await on_progress(msg)
                        elif evt_type == "step_finish":
                            finish_reason = part.get("reason", "stop")
                            usage = part.get("tokens", {})
                    except Exception as e:
                        logger.debug(f"Failed to parse line from OpenCode: {e}")

            await process.wait()
            await stderr_task

            if process.returncode != 0:
                stderr_data = "".join(stderr_buffer)
                return LLMResponse(
                    content=f"Error calling OpenCode CLI (exit code {process.returncode}):\n{stderr_data}",
                    finish_reason="error",
                )

            # Normalize usage tokens for SQLite tracking
            norm_usage = {}
            if usage:
                norm_usage["prompt_tokens"] = usage.get("prompt", 0) or usage.get(
                    "prompt_tokens", 0
                )
                norm_usage["completion_tokens"] = usage.get("completion", 0) or usage.get(
                    "completion_tokens", 0
                )
                norm_usage["total_tokens"] = usage.get("total", 0) or usage.get("total_tokens", 0)

            return LLMResponse(
                content="".join(full_content),
                finish_reason=finish_reason,
                usage=norm_usage,
            )

        except Exception as e:
            logger.exception("OpenCode CLI execution failed")
            return LLMResponse(
                content=f"Exception calling OpenCode CLI: {str(e)}",
                finish_reason="error",
            )

    def get_default_model(self) -> str:
        """Get the default model identifier."""
        return self.default_model
