from __future__ import annotations

from dataclasses import dataclass

from collector.core.profile import BankProfile
from collector.drivers.base import BankDriver


@dataclass(frozen=True)
class BankEntry:
    code: str
    name: str
    module: str = ""
    class_name: str = ""

    @property
    def implemented(self) -> bool:
        return bool(self.module and self.class_name)


BANKS: tuple[BankEntry, ...] = (
    BankEntry("citic", "中信银行", "collector.drivers.citic", "CiticDriver"),
    BankEntry("cib", "兴业银行", "collector.drivers.cib", "CibDriver"),
    BankEntry("icbc", "工商银行", "collector.drivers.icbc", "IcbcDriver"),
    BankEntry("nbcb", "宁波银行财资云"),
    BankEntry("njcb", "南京银行"),
    BankEntry("cgb", "广发银行"),
    BankEntry("mock", "模拟银行", "collector.drivers.mock", "MockDriver"),
)

_REGISTRY: dict[str, BankEntry] = {entry.code: entry for entry in BANKS}


def list_banks() -> list[dict]:
    return [
        {"code": entry.code, "name": entry.name, "implemented": entry.implemented}
        for entry in BANKS
    ]


def get_driver(bank_code: str, profile_path: str | None = None) -> BankDriver:
    entry = _REGISTRY.get(bank_code)
    if entry is None:
        raise KeyError(f"未注册的银行: {bank_code}")
    if not entry.implemented:
        raise NotImplementedError(f"{entry.name}（{entry.code}）采集驱动尚未接入平台")
    module = __import__(entry.module, fromlist=[entry.class_name])
    driver_cls = getattr(module, entry.class_name)
    return driver_cls(BankProfile.load(bank_code, profile_path))
