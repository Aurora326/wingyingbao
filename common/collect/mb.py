"""MB 机柜：复用仓库根目录 common/cabinet_controller.py 的实现。

这里只做一层薄适配——保留采集器一直用的 connect/disconnect 语义和 MbError，
命令拼装、mb.exe 定位、JSON 解析都在 common 里，老项目和新采集器共用同一份。
"""
from __future__ import annotations

import time

from common.cabinet_controller import MBCabinetController


class MbError(RuntimeError):
    pass


class MbController:
    """MB 在线机柜控制器（通过本地 mb.exe 子命令操作）"""

    def __init__(self, exe: str = "", ter_id: str = "", port: int = 1, user: str = "", timeout: int = 30) -> None:
        self.ter_id = ter_id
        self.port = int(port or 1)
        self.user = user
        self.timeout = timeout
        self._ctl = MBCabinetController(
            {"mb_path": exe or "mb", "ter_id": ter_id, "port": self.port, "user": user}
        )

    @property
    def exe(self) -> str:
        return self._ctl.mb_path

    def _run(self, subcmd: str) -> dict:
        # common 的公开方法只返回 bool，这里要拿到失败原因，所以直接走子命令
        data = self._ctl._execute_subcommand(subcmd)
        if not data.get("success"):
            detail = data.get("error") or data.get("output") or "未知错误"
            raise MbError(f"mb.exe {subcmd} 失败: {str(detail)[:300]}")
        return data

    def connect(self) -> bool:
        self._run("load_port")
        return True

    def disconnect(self) -> bool:
        self._run("shutdown_port")
        return True

    def press(self) -> bool:
        self._run("robot")
        return True

    def status(self) -> dict:
        return self._run("status")

    def wait_ready(self, seconds: int = 10) -> None:
        if seconds > 0:
            time.sleep(seconds)
