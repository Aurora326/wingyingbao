from __future__ import annotations

import subprocess


class Gf3Error(RuntimeError):
    pass


class Gf3Controller:
    FLAG_PRESS = 1
    FLAG_CONNECT = 2
    FLAG_DISCONNECT = 3

    def __init__(self, exe: str = "gf3", cabinet: int = 1, enabled: bool = True, timeout: int = 20) -> None:
        self.exe = exe
        self.cabinet = cabinet
        self.enabled = enabled
        self.timeout = timeout

    def _run(self, port: int, flag: int) -> bool:
        if not self.enabled:
            return True
        cmd = [self.exe, str(self.cabinet), str(port), str(flag)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="gbk", timeout=self.timeout)
        except FileNotFoundError as exc:
            raise Gf3Error(f"未找到 gf3 可执行文件: {self.exe}") from exc
        except subprocess.TimeoutExpired as exc:
            raise Gf3Error(f"gf3 命令超时: {cmd}") from exc
        if proc.returncode != 0 or "命令执行成功" not in (proc.stdout or ""):
            raise Gf3Error(f"gf3 执行失败: {cmd} -> {proc.stdout}{proc.stderr}")
        return True

    def connect(self, port: int) -> bool:
        return self._run(port, self.FLAG_CONNECT)

    def disconnect(self, port: int) -> bool:
        return self._run(port, self.FLAG_DISCONNECT)

    def press(self, port: int) -> bool:
        return self._run(port, self.FLAG_PRESS)

    def isolate(self, port: int, ports: range = range(1, 13)) -> None:
        for other in ports:
            if other == port:
                continue
            try:
                self.disconnect(other)
            except Gf3Error:
                continue
        self.connect(port)
