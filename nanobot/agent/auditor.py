"""Auditor for security evaluation of shell commands."""

from typing import Literal

from loguru import logger

from nanobot.providers.base import LLMProvider


class CommandAuditor:
    """
    The Auditor is a secondary LLM pipeline that evaluates
    shell commands for destructive actions.
    """

    def __init__(self, provider: LLMProvider, model: str):
        self.provider = provider
        self.model = model
        self.system_prompt = (
            "You are a strict security auditor. Your ONLY job is to evaluate if a shell command "
            "is SAFE or UNSAFE to execute on the host system. "
            "Deny any command that uses 'rm -rf', 'sudo', 'mkfs', 'dd', or attempts to access secrets/keys. "
            "Reply with exactly one word: SAFE or UNSAFE."
        )

    async def evaluate(self, command: str) -> Literal["SAFE", "UNSAFE"]:
        try:
            response = await self.provider.chat(
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {
                        "role": "user",
                        "content": f"Evaluate this command for destructive actions: {command}\nReply SAFE or UNSAFE.",
                    },
                ],
                model=self.model,
                max_tokens=10,
                temperature=0.0,
            )
            result = (response.content or "").strip().upper()
            return "SAFE" if "SAFE" in result and "UNSAFE" not in result else "UNSAFE"
        except Exception:
            logger.exception("Auditor execution failed")
            return "UNSAFE"
