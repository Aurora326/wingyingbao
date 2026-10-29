from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path


class MbError(RuntimeError):
    pass


MB_CANDIDATES = [
    r"C:\wangyingbao\m2\mb.exe",
    r"C:\wangyingbao\tools\mb.exe",
]


class MbController:
    """MB 在线机柜控制器（通过本地 mb.exe 子命令操作）"""

    def __init__(self, exe: str = "", ter_id: str = "", port: int = 1, user: str = "", timeout: int = 30) -> None:
        self.exe = self._find_exe(exe)
        self.ter_id = ter_id
        self.port = int(port or 1)
        self.user = user
        self.timeout = timeout

    @staticmethod
    def _find_exe(explicit: str) -> str:
        candidates = []
        if explicit:
            candidates.append(explicit)
        env = os.getenv("MB_EXE", "")
        if env:
            candidates.append(env)
        candidates.extend(MB_CANDIDATES)
        for candidate in candidates:
            try:
                if Path(candidate).exists():
                    return candidate
            except OSError:
                continue
        return shutil.which("mb.exe") or explicit or "mb.exe"

    def _run(self, subcmd: str, with_port: bool = True, with_user: bool = False) -> dict:
        cmd = [self.exe, subcmd]
        if subcmd not in ("find_ter", "get_mac"):
            cmd.extend(["--ter_id", self.ter_id or ""])
        if with_port:
            cmd.extend(["--port", str(self.port)])
        if with_user and self.user:
            cmd.extend(["--user", self.user])
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                cwd=str(Path(self.exe).parent) if Path(self.exe).parent.exists() else None,
            )
        except FileNotFoundError as exc:
            raise MbError(f"未找到 mb.exe: {self.exe}") from exc
        except subprocess.TimeoutExpired as exc:
            raise MbError(f"mb.exe 执行超时: {' '.join(cmd)}") from exc
        if proc.returncode != 0:
            raise MbError(f"mb.exe {subcmd} 失败: {(proc.stderr or proc.stdout or '').strip()[:300]}")
        text = (proc.stdout or "").strip()
        if not text:
            return {"success": True}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {"success": True, "output": text}
        if not isinstance(data, dict):
            return {"success": True, "data": data}
        if "result" in data:
            ok = bool(data.get("result"))
        else:
            ok = bool(data.get("success", True))
        if not ok:
            detail = data.get("error") or data.get("data") or "未知错误"
            raise MbError(f"mb.exe {subcmd} 失败: {detail}")
        data["success"] = True
        return data

    def connect(self) -> bool:
        return bool(self._run("load_port", with_user=True).get("success", True))

    def disconnect(self) -> bool:
        return bool(self._run("shutdown_port", with_user=True).get("success", True))

    def press(self) -> bool:
        return bool(self._run("robot", with_user=False).get("success", True))

    def status(self) -> dict:
        return self._run("status", with_port=False)

    def wait_ready(self, seconds: int = 10) -> None:
        if seconds > 0:
            time.sleep(seconds)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.disconnect()
        except Exception:
            pass
        return False
