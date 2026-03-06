import re
from datetime import datetime, timezone
from pathlib import Path


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_data_path() -> Path:
    return ensure_dir(Path.home() / ".nanobot")


def get_workspace_path(workspace: str | None = None) -> Path:
    path = Path(workspace).expanduser() if workspace else Path.home() / ".nanobot" / "workspace"
    return ensure_dir(path)


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


_UNSAFE_CHARS = re.compile('[<>:"/\\\\|?*]')

_MAX_FILENAME_LEN = 200


def safe_filename(name: str) -> str:
    result = _UNSAFE_CHARS.sub("_", name).strip()
    if not result or result in (".", ".."):
        result = "_"
    return result[:_MAX_FILENAME_LEN]


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
    if text is None:
        return None
    result = re.sub("<think>[\\s\\S]*?</think>", "", text).strip()
    return result or None
