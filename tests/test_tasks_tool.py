import json

import pytest

from nanobot.agent.tools.tasks import TaskTool


class TestTaskTool:
    @pytest.fixture
    def tool(self, temp_workspace):
        return TaskTool(workspace=temp_workspace)

    @pytest.mark.asyncio
    async def test_add_task(self, tool, temp_workspace):
        result = await tool.execute(action="add", id="t1", title="Test Task", priority="high")
        assert "added" in result.lower()

        task_file = temp_workspace / "tasks" / "t1.json"
        assert task_file.exists()
        data = json.loads(task_file.read_text())
        assert data["title"] == "Test Task"
        assert data["priority"] == "high"
        assert data["status"] == "todo"

    @pytest.mark.asyncio
    async def test_list_tasks(self, tool, temp_workspace):
        await tool.execute(action="add", id="t1", title="Task 1")
        await tool.execute(action="add", id="t2", title="Task 2")

        result = await tool.execute(action="list")
        tasks = json.loads(result)
        assert len(tasks) == 2
        ids = {t["id"] for t in tasks}
        assert ids == {"t1", "t2"}

    @pytest.mark.asyncio
    async def test_update_task(self, tool, temp_workspace):
        await tool.execute(action="add", id="u1", title="Old Title")
        result = await tool.execute(action="update", id="u1", title="New Title", status="doing")
        assert "updated" in result.lower()

        task_file = temp_workspace / "tasks" / "u1.json"
        data = json.loads(task_file.read_text())
        assert data["title"] == "New Title"
        assert data["status"] == "doing"

    @pytest.mark.asyncio
    async def test_mark_done(self, tool, temp_workspace):
        await tool.execute(action="add", id="d1", title="Done Task")
        result = await tool.execute(action="done", id="d1")
        assert "done" in result.lower()

        task_file = temp_workspace / "tasks" / "d1.json"
        data = json.loads(task_file.read_text())
        assert data["status"] == "done"
        assert "completed_at" in data

    @pytest.mark.asyncio
    async def test_task_not_found(self, tool):
        result = await tool.execute(action="update", id="ghost", title="X")
        assert "Error" in result
        assert "not found" in result.lower()
