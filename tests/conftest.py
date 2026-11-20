from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CCB_DIR = REPO_ROOT / "建设银行" / "ccb_automation"

# 离线单测：只挂仓库根与建行目录，不加载 platform/tests/conftest.py（它依赖 PostgreSQL）
for path in (REPO_ROOT, CCB_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
