"""OpenCode CLI provider implementation."""

import asyncio
import json
from typing import Any

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
    ) -> LLMResponse:
        """
        Send a chat completion request via OpenCode CLI.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions (ignored if OpenCode manages them itself,
                   or passed if bridged).
            model: Model identifier (ignored for now as opencode handles it internally).
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.

        Returns:
            LLMResponse with content.
        """
        # Build prompt from conversation history to provide full context
        prompt_parts = []
        for m in messages[:-1]:
            role = m.get("role", "user")
            content = m.get("content") or ""
            if role == "system":
                continue  # System prompt is passed via separate flag
            # Format history in a way that the intelligence engine can easily parse
            prompt_parts.append(f"### {role.upper()}\n{content}")

        last_msg = messages[-1].get("content", "") if messages else ""
        system_msg = next((m.get("content") for m in messages if m.get("role") == "system"), None)

        full_prompt = "\n\n".join(prompt_parts + [str(last_msg)])
        if system_msg:
            full_prompt = f"### SYSTEM\n{system_msg}\n\n{full_prompt}"

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

            if process.stdout:
                async for line in process.stdout:
                    if not line:
                        break
                    try:
                        event = json.loads(line.decode().strip())
                        if event.get("type") == "text":
                            full_content.append(event["part"]["text"])
                        elif event.get("type") == "step_finish":
                            finish_reason = event["part"].get("reason", "stop")
                            usage = event["part"].get("tokens", {})
                    except Exception as e:
                        logger.debug(f"Failed to parse line from OpenCode: {e}")

            await process.wait()

            if process.returncode != 0:
                stderr_data = await process.stderr.read() if process.stderr else b""
                return LLMResponse(
                    content=f"Error calling OpenCode CLI (exit code {process.returncode}):\n{stderr_data.decode()}",
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
