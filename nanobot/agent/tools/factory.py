from pathlib import Path
from typing import TYPE_CHECKING, Any

from .cron import CronTool
from .filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from .message import MessageTool
from .rewrite import RewriteCodeTool
from .shell import ExecTool
from .spawn import SpawnTool
from .system import ReloadTool
from .tasks import TaskTool
from .web import WebFetchTool, WebSearchTool

if TYPE_CHECKING:
    from .registry import ToolRegistry

def register_all_tools(
    registry: "ToolRegistry",
    workspace: Path,
    restrict_to_workspace: bool = False,
    exec_config: Any = None,
    bus: Any = None,
    auditor: Any = None,
    brave_api_key: str | None = None,
    subagents: Any = None,
    cron_service: Any = None,
    send_callback: Any = None,
) -> None:
    allowed_dir = workspace if restrict_to_workspace else None
    registry.register(ReadFileTool(workspace=workspace, allowed_dir=allowed_dir))
    registry.register(WriteFileTool(workspace=workspace, allowed_dir=allowed_dir))
    registry.register(EditFileTool(workspace=workspace, allowed_dir=allowed_dir))
    registry.register(ListDirTool(workspace=workspace, allowed_dir=allowed_dir))
    registry.register(RewriteCodeTool(workspace=workspace, allowed_dir=allowed_dir))

    registry.register(
        ExecTool(
            working_dir=str(workspace),
            timeout=exec_config.timeout if exec_config else 300,
            restrict_to_workspace=restrict_to_workspace,
            path_append=exec_config.path_append if exec_config else None,
            bus=bus,
            auditor=auditor,
            use_docker=exec_config.use_docker if exec_config else False,
            docker_image=exec_config.docker_image if exec_config else None,
        )
    )

    registry.register(WebSearchTool(api_key=brave_api_key))
    registry.register(WebFetchTool())
    registry.register(TaskTool(workspace=workspace))
    registry.register(ReloadTool())

    if cron_service:
        registry.register(CronTool(cron_service))
    if subagents:
        registry.register(SpawnTool(manager=subagents))
    if send_callback:
        registry.register(MessageTool(send_callback=send_callback))
