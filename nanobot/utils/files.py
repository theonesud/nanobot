import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, content: str, mode: str = "w", encoding: str = "utf-8"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode, dir=path.parent, delete=False, encoding=encoding) as f:
        f.write(content)
        t_name = f.name
    os.replace(t_name, path)
