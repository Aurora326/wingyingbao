"""银行码表：只给界面下拉用。

平台不含任何采集逻辑，这里只有 code / 中文名 / 能不能采。
「能采」的判断依据是采集清单里配了这家银行，或者源码目录下有对应实现，
所以加一家银行不需要改平台代码——放一个采集程序进去就自动出现在下拉里。
"""
from __future__ import annotations

from app.config import settings

BANK_NAMES = {
    "citic": "中信银行",
    "cib": "兴业银行",
    "icbc": "工商银行",
    "njcb": "南京银行",
    "nb": "宁波银行",
    "nbcb": "宁波银行财资云",
    "cgb": "广发银行",
    "ccb": "建设银行",
    "mock": "模拟银行",
}


def list_banks(registry: dict | None = None) -> list[dict]:
    if registry is None:
        from app.services import collectors

        registry = collectors.load_registry(settings.collectors_file)
    return [
        {"code": code, "name": name, "implemented": code in registry}
        for code, name in BANK_NAMES.items()
    ]
