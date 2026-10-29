"""工商银行采集入口。

单机运行：python run_task.py --task task.json
平台调用：在 collectors.yaml 里配 cwd 指向本目录、command 为 python run_task.py

密钥不放在 task.json 里，平台通过环境变量传入：
BANK_USERNAME / BANK_PASSWORD / BANK_PIN
"""
import sys
from pathlib import Path

# 仓库根目录挂到路径上，才能 import common.collect（打包后由 PyInstaller 处理）
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.collect.run_task import main  # noqa: E402

from driver import IcbcDriver  # noqa: E402

PROFILE = Path(__file__).resolve().parent / "profiles" / "icbc.yaml"

if __name__ == "__main__":
    raise SystemExit(main(IcbcDriver, PROFILE))
