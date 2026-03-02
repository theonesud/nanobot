from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def get_definitions(self) -> list[dict[str, Any]]:
        return [tool.to_schema() for tool in self._tools.values()]

    async def execute(self, name: str, params: dict[str, Any], workspace: Any | None = None, **kwargs: Any) -> str:
        _hint = "\n\n[Analyze the error above and try a different approach.]"
        tool = self._tools.get(name)
        if not tool:
            logger.error("❌ Tool '{}' not found in registry", name)
            return f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"
        try:
            errors = tool.validate_params(params)
            if errors:
                logger.warning("⚠️ Invalid parameters for tool '{}': {}", name, errors)
                return f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors) + _hint
            merged_args = kwargs.copy()
            merged_args.update(params)
            logger.debug("⚙️ Executing tool: {} with {}", name, params)
            result = await tool.execute(**merged_args)
            res_str = str(result)
            logger.debug("📤 Tool '{}' response ({} chars)", name, len(res_str))
            if res_str.startswith("Error"):
                logger.warning("❌ Tool '{}' returned an error", name)
                if workspace:
                    log_file = workspace / "logs" / "nanobot.log"
                    if log_file.exists():
                        try:
                            lines = log_file.read_text().splitlines()[-15:]
                            res_str += "\n\n--- [INTERNAL LOGS (last 15 lines)] ---\n" + "\n".join(lines)
                        except Exception:
                            pass
                return res_str + _hint
            return result
        except Exception as e:
            logger.exception("💥 Exception in tool '{}'", name)
            res_str = f"Error executing {name}: {str(e)}"
            if workspace:
                log_file = workspace / "logs" / "nanobot.log"
                if log_file.exists():
                    try:
                        lines = log_file.read_text().splitlines()[-15:]
                        res_str += "\n\n--- [INTERNAL LOGS (last 15 lines)] ---\n" + "\n".join(lines)
                    except Exception:
                        pass
            return res_str + _hint


    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
