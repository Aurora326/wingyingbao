"""按文件路径加载各家银行的驱动。

每家银行的驱动都叫 driver.py，直接 import 会互相覆盖，所以按文件路径加载并起唯一模块名。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# bank_code -> (驱动目录, 驱动类名)
BANKS = {
    "icbc": ("_archive/new_driver/icbc", "IcbcDriver"),
    "citic": ("_archive/new_driver/citic", "CiticDriver"),
    "cib": ("_archive/new_driver/cib", "CibDriver"),
    "mock": ("模拟银行", "MockDriver"),
}


def bank_dir(bank_code: str) -> Path:
    return REPO_ROOT / BANKS[bank_code][0]


def profile_path(bank_code: str) -> Path:
    return bank_dir(bank_code) / "profiles" / f"{bank_code}.yaml"


def load_driver_module(bank_code: str):
    name = f"{bank_code}_driver"
    if name in sys.modules:
        return sys.modules[name]
    path = bank_dir(bank_code) / "driver.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def make_driver(bank_code: str):
    from common.collect.profile import BankProfile

    cls = getattr(load_driver_module(bank_code), BANKS[bank_code][1])
    return cls(BankProfile.load(bank_code, profile_path(bank_code)))
