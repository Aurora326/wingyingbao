"""客户机跨进程硬件/桌面互斥锁。

同一台 Windows 客户机上可能同时运行银行采集、制单、付款采集与插件热切，
所有涉及 UKey 机柜端口、桌面键鼠焦点或切换插件指针的操作必须共享同一把文件锁。
"""
from __future__ import annotations

import os
import time
from pathlib import Path


def default_lock_path(work_dir: Path | None = None) -> Path:
    explicit = os.getenv("PLATFORM_AGENT_LOCK_FILE", "").strip()
    if explicit:
        return Path(explicit)
    local_app = os.getenv("LOCALAPPDATA", "").strip()
    if local_app:
        return Path(local_app) / "UKeyAgent" / "run" / "ukey_bus.lock"
    if work_dir is not None:
        return Path(work_dir).resolve().parent / "ukey_bus.lock"
    return Path.home() / ".ukeyagent" / "ukey_bus.lock"


class AgentBusLock:
    """跨进程非阻塞轮询文件锁（零第三方依赖，支持 Windows 与 POSIX）。"""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else default_lock_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = None

    def acquire(self, timeout: float = 1800.0, interval: float = 0.5) -> bool:
        deadline = time.time() + max(0.0, timeout)
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
            time.sleep(min(interval, max(0.05, deadline - time.time())))

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
            try:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        finally:
            try:
                self._handle.close()
            finally:
                self._handle = None

    def __enter__(self) -> "AgentBusLock":
        if not self.acquire():
            raise TimeoutError(f"等待客户机 UKey/桌面互斥锁超时: {self.path}")
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.release()
        return False
