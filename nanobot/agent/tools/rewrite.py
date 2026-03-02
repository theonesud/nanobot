import ast
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.filesystem import _git_commit, _resolve_path


class RewriteCodeTool(Tool):
    name = "rewrite_code"
    description = (
        "Rewrite a Python class or function by name using AST. "
        "More reliable than text replacement for complex files."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the .py file"},
            "symbol": {"type": "string", "description": "Name of class or function to rewrite"},
            "new_code": {"type": "string", "description": "Full source code for the symbol"},
        },
        "required": ["path", "symbol", "new_code"],
    }

    def __init__(self, workspace: Path | None = None, allowed_dir: Path | None = None):
        self._workspace = workspace
        self._allowed_dir = allowed_dir

    async def execute(self, path: str, symbol: str, new_code: str, **kwargs: Any) -> str:
        try:
            file_path = _resolve_path(path, self._workspace, self._allowed_dir)
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            lines = source.splitlines(keepends=True)
            target_node = None
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.name == symbol:
                        target_node = node
                        break
            if not target_node:
                return f"Error: Symbol '{symbol}' not found in {path}"
            start, end = target_node.lineno - 1, target_node.end_lineno
            # Preserve indentation of the original symbol if possible
            indent = ""
            if start < len(lines):
                m = __import__("re").match(r"^\s*", lines[start])
                indent = m.group(0) if m else ""
            indented_code = "\n".join(
                [(indent + line if i > 0 else line) for i, line in enumerate(new_code.splitlines())]
            )
            if not indented_code.endswith("\n") and end < len(lines):
                indented_code += "\n"
            lines[start:end] = [indented_code]
            file_path.write_text("".join(lines), encoding="utf-8")
            _git_commit(file_path, f"nanobot: rewrite {symbol} in {file_path.name}")
            return f"Successfully rewrote '{symbol}' in {path}"
        except Exception as e:
            return f"Error: {e}"
