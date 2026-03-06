import asyncio
import os
import time
from pathlib import Path

from loguru import logger

_STALE_LOCK_AGE_SECONDS = 600


class FileLock:
    def __init__(self, lock_file: Path, timeout: float = 10.0):
        self.lock_file = lock_file
        self.timeout = timeout
        self._locked = False

    async def __aenter__(self):
        start_time = time.time()
        while time.time() - start_time < self.timeout:
            try:
                fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w") as f:
                    f.write(f"{os.getpid()}:{time.time()}")
                self._locked = True
                return self
            except FileExistsError:
                try:
                    with open(self.lock_file, "r") as f:
                        parts = f.read().strip().split(":")
                        pid = int(parts[0])
                        created = float(parts[1]) if len(parts) > 1 else 0.0
                    is_stale = (
                        not self._is_running(pid)
                        or (created > 0 and time.time() - created > _STALE_LOCK_AGE_SECONDS)
                    )
                    if is_stale:
                        logger.warning("Deleting stale lock file: {}", self.lock_file)
                        try:
                            os.remove(self.lock_file)
                        except FileNotFoundError:
                            pass
                        continue
                except (ValueError, FileNotFoundError):
                    pass
                await asyncio.sleep(0.1)
        raise TimeoutError(f"Could not acquire lock on {self.lock_file} after {self.timeout}s")

    async def __aexit__(self, *args):
        if self._locked:
            try:
                os.remove(self.lock_file)
            except FileNotFoundError:
                pass
            self._locked = False

    @staticmethod
    def _is_running(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
