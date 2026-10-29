from __future__ import annotations

import time
from pathlib import Path


class BusLock:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = None

    def acquire(self, timeout: int = 1800, interval: float = 1.0) -> bool:
        deadline = time.time() + timeout
        while True:
            handle = open(self.path, "a+b")
            try:
                if self._try_lock(handle):
                    self._handle = handle
                    return True
            except OSError:
                pass
            handle.close()
            if time.time() >= deadline:
                return False
            time.sleep(interval)

    @staticmethod
    def _try_lock(handle) -> bool:
        try:
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except ImportError:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                return False
        except OSError:
            return False

    def release(self) -> None:
        if not self._handle:
            return
        try:
            import msvcrt

            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception:
            pass
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self):
        if not self.acquire():
            raise TimeoutError("获取采集总线锁超时")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False
