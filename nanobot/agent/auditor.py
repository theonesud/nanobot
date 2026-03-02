from typing import Literal

from loguru import logger

from nanobot.providers.base import LLMProvider


class CommandAuditor:
    def __init__(self, provider: LLMProvider, model: str):
        self.provider = provider
        self.model = model
        self.system_prompt = "Evaluate if a shell command is SAFE or UNSAFE. Deny commands using: rm -rf, sudo, mkfs, dd, or accessing secrets/keys. Reply with exactly one word: SAFE or UNSAFE."

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
            if "SAFE" in result and "UNSAFE" not in result:
                return "SAFE"
            return "UNSAFE"
        except Exception:
            logger.exception("Auditor execution failed")
            return "UNSAFE"
