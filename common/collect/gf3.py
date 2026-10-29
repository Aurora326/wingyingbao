"""GF3 机柜：复用仓库根目录 common/cabinet_controller.py 的实现。

common 的 GF3 控制器一个实例绑一个端口，这里按端口临时构造，
对外仍保留采集器用的 isolate/connect/disconnect 语义。
"""
from __future__ import annotations

from common.cabinet_controller import GF3CabinetController


class Gf3Error(RuntimeError):
    pass


class Gf3Controller:
    def __init__(self, exe: str = "gf3", cabinet: int = 1, enabled: bool = True, timeout: int = 20) -> None:
        self.exe = exe
        self.cabinet = int(cabinet or 1)
        self.enabled = enabled
        self.timeout = timeout

    def _ctl(self, port: int) -> GF3CabinetController:
        return GF3CabinetController(
            {"gf3_path": self.exe, "product_id": self.cabinet, "port": int(port or 1)}
        )

    def _run(self, port: int, action: str) -> bool:
        if not self.enabled:
            return True
        if not getattr(self._ctl(port), action)():
            raise Gf3Error(f"gf3 {action} 失败: cabinet={self.cabinet} port={port}")
        return True

    def connect(self, port: int) -> bool:
        return self._run(port, "connect")

    def disconnect(self, port: int) -> bool:
        return self._run(port, "close")

    def press(self, port: int) -> bool:
        return self._run(port, "click_ok")

    def isolate(self, port: int, ports: range = range(1, 13)) -> None:
        """把目标端口的 UKey 独占出来：先断其他端口，再连目标端口。"""
        for other in ports:
            if other == port:
                continue
            try:
                self.disconnect(other)
            except Gf3Error:
                continue
        self.connect(port)
