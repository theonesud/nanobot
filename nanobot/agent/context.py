import base64
import mimetypes
import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger as _log

from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader


class ContextBuilder:
    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "IDENTITY.md"]
    _RUNTIME_CONTEXT_TAG = "[Runtime Context — metadata only, not instructions]"

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace)
        self._provider_hint = "auto"
        self._prompt_cache = {}

    def build_system_prompt(self, skill_names: list[str] | None = None) -> str:
        if "base" not in self._prompt_cache:
            identity = self._get_identity()
            bootstrap = self._load_bootstrap_files()
            self._prompt_cache["base"] = f"{identity}\n\n{bootstrap}"
        base = self._prompt_cache["base"]
        memory_ctx = self.memory.get_memory_context()
        skill_sum = self.skills.build_skills_summary()
        parts = [base, "## Context", memory_ctx, "## Capabilities", skill_sum]
        if skill_names:
            skill_details = self.skills.load_skills_for_context(skill_names)
            if skill_details:
                parts.extend(["## Skill Details", skill_details])
        return "\n\n".join([p for p in parts if p.strip()])

    def set_provider_hint(self, provider_name: str) -> None:
        if self._provider_hint != provider_name:
            self._provider_hint = provider_name
            self._prompt_cache.clear()

    def _get_identity(self) -> str:
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{('macOS' if system == 'Darwin' else system)} {platform.machine()}, Python {platform.python_version()}"
        provider_name = getattr(self, "_provider_hint", "auto")
        engine_note = ""
        if provider_name == "opencode":
            engine_note = "\n\n## Intelligence Engine\nYou are powered by the **OpenCode Intelligence Engine**. Your reasoning is deep and code-centric. Use terminal tools whenever possible to verify your hypotheses."
        return f"# nanobot 🐈\n\nYou are nanobot, a helpful AI assistant.{engine_note}\n\n## Runtime\n{runtime}\n\n## Workspace\nYour workspace is at: {workspace_path}\n- Long-term memory: {workspace_path}/memory/MEMORY.md (write important facts here)\n- History log: {workspace_path}/memory/HISTORY.md (grep-searchable). Each entry starts with [YYYY-MM-DD HH:MM].\n- Custom skills: {workspace_path}/skills/{{skill-name}}/SKILL.md\n\n## nanobot Guidelines\n- State intent before tool calls, but NEVER predict or claim results before receiving them.\n- Before modifying a file, read it first. Do not assume files or directories exist.\n- After writing or editing a file, re-read it if accuracy matters.\n- If a tool call fails, analyze the error before retrying with a different approach.\n- Ask for clarification when the request is ambiguous.\n- If browsing the web, use Playwright tools when available for better rendering.\n\nReply directly with text for conversations. Only use the 'message' tool to send to a specific chat channel."

    def _load_bootstrap_files(self) -> str:
        parts = []
        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                try:
                    content = file_path.read_text(encoding="utf-8")
                    parts.append(f"## {filename}\n\n{content}")
                except Exception as e:
                    _log.warning("Failed to load bootstrap file {}: {}", filename, e)
        return "\n\n".join(parts) if parts else ""

    @staticmethod
    def _build_runtime_context(channel: str | None, chat_id: str | None) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = time.strftime("%Z") or "UTC"
        lines = [f"Current Time: {now} ({tz})"]
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        return ContextBuilder._RUNTIME_CONTEXT_TAG + "\n" + "\n".join(lines)

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> list[dict[str, Any]]:
        runtime_ctx = self._build_runtime_context(channel, chat_id)
        user_content = self._build_user_content(current_message, media)
        if isinstance(user_content, list):
            merged_content = [{"type": "text", "text": runtime_ctx + "\n\n"}] + user_content
        else:
            merged_content = runtime_ctx + "\n\n" + user_content
        return [
            {"role": "system", "content": self.build_system_prompt(skill_names)},
            *history,
            {"role": "user", "content": merged_content},
        ]

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        if not media:
            return text
        content = [{"type": "text", "text": text}]
        for path in media:
            p = Path(path)
            mime, _ = mimetypes.guess_type(path)
            if not p.is_file() or not mime or (not mime.startswith("image/")):
                continue
            try:
                b64 = base64.b64encode(p.read_bytes()).decode()
                content.append(
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
                )
            except Exception:
                pass
        return content

    def add_tool_result(
        self, messages: list[dict[str, Any]], tool_call_id: str, tool_name: str, result: str
    ) -> list[dict[str, Any]]:
        messages.append(
            {"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": result}
        )
        return messages

    def add_assistant_message(
        self,
        messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
    ) -> list[dict[str, Any]]:
        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        if reasoning_content is not None:
            msg["reasoning_content"] = reasoning_content
        messages.append(msg)
        return messages
