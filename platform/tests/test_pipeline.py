from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app.models import BankAccount, CollectionTask, Enterprise, Receipt, Statement
from app.services.executor import run_task
from app.services.ingest import ingest_receipt
from app.services.scheduler import enqueue_task
from tests.test_parsers import REAL_CIB_RECEIPT


def _use_mock_collector(monkeypatch):
    """测试里显式声明模拟银行的采集程序，平台本身不猜路径。"""
    import sys

    from app.services import collectors
    from bank_loader import bank_dir

    monkeypatch.setattr(
        collectors,
        "load_registry",
        lambda path: {"mock": {"command": [sys.executable, "run_task.py"], "cwd": str(bank_dir("mock"))}},
    )



def test_mock_task_end_to_end(db, monkeypatch):
    _use_mock_collector(monkeypatch)
    from app.seed import seed

    seed(db)
    account = db.execute(select(BankAccount).where(BankAccount.bank_code == "mock")).scalars().first()
    task = enqueue_task(
        db, account, "statements", dt.date(2026, 1, 1), dt.date(2026, 1, 31), trigger="manual", created_by="test", force=True
    )
    run_task(task.id)

    db.expire_all()
    finished = db.get(CollectionTask, task.id)
    assert finished.status == "succeeded"
    assert finished.error_code == ""

    rows = db.execute(select(Statement).where(Statement.account_id == account.id)).scalars().all()
    assert len(rows) == 3
    assert {row.serial_no for row in rows} == {"SC0001", "SC0002", "SC0003"}

    again = enqueue_task(
        db, account, "statements", dt.date(2026, 1, 1), dt.date(2026, 1, 31), trigger="manual", created_by="test", force=True
    )
    run_task(again.id)
    db.expire_all()
    rows_after = db.execute(select(Statement).where(Statement.account_id == account.id)).scalars().all()
    assert len(rows_after) == 3, "重复采集不应产生重复流水"


def test_mock_receipts_are_idempotent(db, monkeypatch):
    _use_mock_collector(monkeypatch)
    from app.seed import seed

    seed(db)
    account = db.execute(select(BankAccount).where(BankAccount.bank_code == "mock")).scalars().first()
    for _ in range(2):
        task = enqueue_task(
            db, account, "receipts", dt.date(2026, 1, 1), dt.date(2026, 1, 31), trigger="manual", created_by="test", force=True
        )
        run_task(task.id)
    db.expire_all()
    rows = db.execute(select(Receipt).where(Receipt.account_id == account.id)).scalars().all()
    assert len(rows) == 1, "同一回单文件重复采集不应重复入库"


def test_multi_receipt_pdf_ingest_is_idempotent(db):
    enterprise = Enterprise(name="回单测试企业")
    db.add(enterprise)
    db.flush()
    account = BankAccount(
        enterprise_id=enterprise.id,
        bank_code="cib",
        account_no="PDFTEST0001",
        account_name="回单测试账户",
    )
    db.add(account)
    db.commit()

    first = ingest_receipt(db, account, REAL_CIB_RECEIPT, None, "receipts/2026/02/cib.pdf")
    db.commit()
    assert first.inserted == 7
    assert first.failed == 0

    second = ingest_receipt(db, account, REAL_CIB_RECEIPT, None, "receipts/2026/02/cib.pdf")
    assert second.inserted == 0
    assert second.duplicates == 7


def test_task_range_watermark_and_span_cap():
    """水位只在真正取到数据时推进；没有水位或水位过旧时，查询跨度要有上限。"""
    from app.services.scheduler import MAX_QUERY_DAYS, task_range

    account = BankAccount(bank_code="icbc", account_no="RANGE001", days_back=30)
    today = dt.date(2026, 10, 29)

    assert task_range(account, today) == (today - dt.timedelta(days=30), today)

    account.watermark_date = dt.date(2026, 10, 28)
    assert task_range(account, today) == (dt.date(2026, 10, 25), today)

    account.watermark_date = dt.date(2025, 1, 1)
    assert task_range(account, today) == (today - dt.timedelta(days=MAX_QUERY_DAYS), today)


def test_no_data_does_not_advance_watermark():
    """查询为空时不得推进水位，否则区间会永远收缩在最近几天。"""
    from app.services.executor import advance_watermark

    class _Account:
        watermark_date = dt.date(2026, 10, 1)

    account = _Account()
    advance_watermark(account, dt.date(2026, 10, 29), no_data=True)
    assert account.watermark_date == dt.date(2026, 10, 1)

    advance_watermark(account, dt.date(2026, 10, 29), no_data=False)
    assert account.watermark_date == dt.date(2026, 10, 29)
