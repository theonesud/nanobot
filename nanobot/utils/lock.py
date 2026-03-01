import asyncio
import os
import time
from pathlib import Path

from loguru import logger


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
                    f.write(str(os.getpid()))
                self._locked = True
                return self
            except FileExistsError:
                try:
                    with open(self.lock_file, "r") as f:
                        pid = int(f.read().strip())
                    if not self._is_running(pid):
                        logger.warning("Deleting stale lock file: {}", self.lock_file)
                        os.remove(self.lock_file)
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
