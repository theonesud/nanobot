import re
from datetime import datetime
from importlib.resources import files as pkg_files
from pathlib import Path

from rich.console import Console


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_data_path() -> Path:
    return ensure_dir(Path.home() / ".nanobot")


def get_workspace_path(workspace: str | None = None) -> Path:
    path = Path(workspace).expanduser() if workspace else Path.home() / ".nanobot" / "workspace"
    return ensure_dir(path)


def timestamp() -> str:
    return datetime.now().isoformat()


_UNSAFE_CHARS = re.compile('[<>:"/\\\\|?*]')


def safe_filename(name: str) -> str:
    return _UNSAFE_CHARS.sub("_", name).strip()


def sync_workspace_templates(workspace: Path, silent: bool = False) -> list[str]:
    try:
        tpl = pkg_files("nanobot") / "templates"
    except Exception:
        return []
    if not tpl.is_dir():
        return []
    added: list[str] = []

    def _write(src, dest: Path):
        if dest.exists():
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8") if src else "", encoding="utf-8")
        added.append(str(dest.relative_to(workspace)))

    for item in tpl.iterdir():
        if item.name.endswith(".md"):
            _write(item, workspace / item.name)
    _write(tpl / "memory" / "MEMORY.md", workspace / "memory" / "MEMORY.md")
    _write(None, workspace / "memory" / "HISTORY.md")
    (workspace / "skills").mkdir(exist_ok=True)
    if added and (not silent):
        for name in added:
            Console().print(f"  [dim]Created {name}[/dim]")
    return added


def get_model_pricing(model: str) -> tuple[float, float]:
    m = model.lower()
    if "opus" in m:
        return (15.0, 75.0)
    if "sonnet" in m:
        return (3.0, 15.0)
    if "haiku" in m:
        return (0.25, 1.25)
    if "gpt-4o" in m:
        return (2.5, 10.0)
    if "gpt-4-turbo" in m:
        return (10.0, 30.0)
    if "gpt-3.5" in m:
        return (0.5, 1.5)
    if "deepseek" in m:
        return (0.27, 1.1)
    if "gemini-1.5-pro" in m:
        return (1.25, 5.0)
    if "gemini-1.5-flash" in m:
        return (0.075, 0.3)
    return (5.0, 15.0)


def strip_think(text: str | None) -> str | None:
    if not text:
        return None
    return re.sub("<think>[\\s\\S]*?</think>", "", text).strip() or None
