import json
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.filesystem import _git_commit


class TaskTool(Tool):
    name = "manage_tasks"
    description = "Add, list, update or complete long-term tasks in the persistent task board."
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "list", "update", "done"]},
            "id": {"type": "string", "description": "Short ID (e.g. 'ui-fix')"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "priority": {"type": "string", "enum": ["low", "medium", "high"]},
            "status": {"type": "string", "enum": ["todo", "doing", "done", "blocked"]},
        },
        "required": ["action"],
    }

    def __init__(self, workspace: Path):
        self.tasks_dir = workspace / "tasks"

    async def execute(self, action: str, **kwargs: Any) -> str:
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        tid = kwargs.get("id", "").strip().lower().replace(" ", "-")
        path = self.tasks_dir / f"{tid}.json" if tid else None

        if action == "add":
            if not tid or not kwargs.get("title"):
                return "Error: ID and Title required for 'add'"
            if path.exists():
                return f"Error: Task '{tid}' already exists."
            task = {
                "id": tid,
                "title": kwargs["title"],
                "description": kwargs.get("description", ""),
                "priority": kwargs.get("priority", "medium"),
                "status": "todo",
                "created_at": __import__("datetime").datetime.now().isoformat(),
            }
            path.write_text(json.dumps(task, indent=2), encoding="utf-8")
            _git_commit(path, f"nanobot: add task {tid}")
            return f"Task '{tid}' added."

        if action == "list":
            tasks = []
            for f in self.tasks_dir.glob("*.json"):
                try:
                    tasks.append(json.loads(f.read_text(encoding="utf-8")))
                except Exception:
                    continue
            if not tasks:
                return "No active tasks."
            return json.dumps(tasks, indent=2)

        if action == "update":
            if not path or not path.exists():
                return f"Error: Task '{tid}' not found."
            task = json.loads(path.read_text(encoding="utf-8"))
            for key in ("title", "description", "priority", "status"):
                if val := kwargs.get(key):
                    task[key] = val
            path.write_text(json.dumps(task, indent=2), encoding="utf-8")
            _git_commit(path, f"nanobot: update task {tid}")
            return f"Task '{tid}' updated."

        if action == "done":
            if not path or not path.exists():
                return f"Error: Task '{tid}' not found."
            task = json.loads(path.read_text(encoding="utf-8"))
            task["status"] = "done"
            task["completed_at"] = __import__("datetime").datetime.now().isoformat()
            path.write_text(json.dumps(task, indent=2), encoding="utf-8")
            _git_commit(path, f"nanobot: complete task {tid}")
            return f"Task '{tid}' marked as done."

        return f"Error: Unknown action '{action}'"
