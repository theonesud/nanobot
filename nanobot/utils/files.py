import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, content: str, mode: str = "w", encoding: str = "utf-8"):
    path.parent.mkdir(parents=True, exist_ok=True)
    t_name = None
    try:
        kw = {"encoding": encoding} if "b" not in mode else {}
        with tempfile.NamedTemporaryFile(mode, dir=path.parent, delete=False, **kw) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
            t_name = f.name
        os.replace(t_name, path)
    except BaseException:
        if t_name:
            try:
                os.unlink(t_name)
            except OSError:
                pass
        raise
