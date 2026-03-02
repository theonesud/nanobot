import json
from pathlib import Path

from nanobot.config.schema import Config
from nanobot.utils.helpers import get_data_path


def get_config_path() -> Path:
    return Path.home() / ".nanobot" / "config.json"


def get_data_dir() -> Path:
    return get_data_path()


def load_config(config_path: Path | None = None) -> Config:
    path = config_path or get_config_path()
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data = _migrate_config(data)
            return Config.model_validate(data)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Failed to load config from {path}: {e}")
            print("Using default configuration.")
    config = Config()
    save_config(config, path)
    return config


def save_config(config: Config, config_path: Path | None = None) -> None:
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(by_alias=True)
    from nanobot.utils.files import atomic_write

    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False))


def _migrate_config(data: dict) -> dict:
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")
    return data
