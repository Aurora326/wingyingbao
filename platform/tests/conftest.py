from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# 采集骨架在 common.collect，测试里要 import 得先把仓库根挂到路径上
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="bank_platform_test_"))
os.environ["PLATFORM_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["PLATFORM_ENABLE_WORKER"] = "0"
os.environ["PLATFORM_ENABLE_SCHEDULER"] = "0"
os.environ["PLATFORM_ENABLE_MOCK_BANK"] = "1"

# 测试只跑 PostgreSQL；默认连接本机 compose 里的 postgres，库名独立，不碰正式库
TEST_DB_URL = os.environ.get(
    "PLATFORM_TEST_DB_URL",
    "postgresql+psycopg://platform:platform@127.0.0.1:5432/platform_test",
)
os.environ["PLATFORM_DB_URL"] = TEST_DB_URL

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402


def _reset_test_database() -> None:
    """每个测试会话重建测试库的 public schema，保证从空库开始。"""
    url = make_url(TEST_DB_URL)
    if not url.database or not url.database.endswith("_test"):
        raise RuntimeError("测试数据库名必须以 _test 结尾，避免误清正式库")
    try:
        admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            exists = connection.execute(
                text("select 1 from pg_database where datname = :name"), {"name": url.database}
            ).scalar()
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{url.database}"'))
        admin.dispose()
        engine = create_engine(url, isolation_level="AUTOCOMMIT")
        with engine.connect() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        engine.dispose()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "测试需要可用的 PostgreSQL：请先启动 docker compose 的 postgres 服务，"
            "或通过 PLATFORM_TEST_DB_URL 指定测试库"
        ) from exc


_reset_test_database()

from app.db import SessionLocal, init_db  # noqa: E402

REAL_CITIC = Path(r"C:\wangyingbao\中信网银\citic_automation\downloads\statements\中信银行_银行流水_20260228.xlsx")
REAL_CIB = Path(r"C:\wangyingbao\兴业网银\cib_automation\downloads\statements\兴业银行_银行流水_20260207.zip")


@pytest.fixture(scope="session", autouse=True)
def _database():
    init_db()
    yield


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
