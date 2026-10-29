from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# 采集程序是独立项目，测试里要 import bankcore 得先把它挂到路径上
COLLECTORS_ROOT = Path(__file__).resolve().parents[2] / "collectors"
sys.path.insert(0, str(COLLECTORS_ROOT))

TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="bank_platform_test_"))
os.environ["PLATFORM_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["PLATFORM_ENABLE_WORKER"] = "0"
os.environ["PLATFORM_ENABLE_SCHEDULER"] = "0"

import pytest  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Base  # noqa: E402

REAL_CITIC = Path(r"C:\wangyingbao\中信网银\citic_automation\downloads\statements\中信银行_银行流水_20260228.xlsx")
REAL_CIB = Path(r"C:\wangyingbao\兴业网银\cib_automation\downloads\statements\兴业银行_银行流水_20260207.zip")


@pytest.fixture(scope="session", autouse=True)
def _database():
    init_db()
    yield
    from app.db import engine

    Base.metadata.drop_all(engine)


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(scope="session", autouse=True)
def _cleanup():
    yield
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)
