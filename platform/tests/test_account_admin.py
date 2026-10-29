from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import (
    AuditLog,
    BankAccount,
    CollectionTask,
    Credential,
    Enterprise,
    Statement,
)
from app.services.account_admin import (
    AccountAdminError,
    account_stats,
    delete_account,
    toggle_collection,
)


def _enterprise(db) -> Enterprise:
    enterprise = db.execute(select(Enterprise).order_by(Enterprise.id)).scalars().first()
    if enterprise is None:
        enterprise = Enterprise(name="删除测试企业")
        db.add(enterprise)
        db.flush()
    return enterprise


def _account(db, number: str, enabled: bool = True) -> BankAccount:
    account = BankAccount(
        enterprise_id=_enterprise(db).id,
        bank_code="mock",
        account_no=number,
        account_name=f"测试账户 {number}",
        collect_enabled=enabled,
    )
    db.add(account)
    db.flush()
    db.add(Credential(bank_account_id=account.id, username_enc="", password_enc="", pin_enc=""))
    db.commit()
    return account


def test_delete_empty_account_requires_account_number(db):
    account = _account(db, "DEL0001")
    db.add(
        CollectionTask(
            account_id=account.id,
            type="statements",
            start_date=dt.date(2026, 1, 1),
            end_date=dt.date(2026, 1, 31),
            status="succeeded",
            idempotency_key="delete-test-1",
        )
    )
    db.commit()
    stats = account_stats(db, account.id)
    assert stats["tasks"] == 1
    assert stats["has_data"] is False
    assert stats["active_tasks"] == 0

    with pytest.raises(AccountAdminError, match="完整账号"):
        delete_account(db, account.id, "wrong")
    assert db.get(BankAccount, account.id) is not None

    result = delete_account(db, account.id, "DEL0001")
    assert result["ok"] is True
    assert db.get(BankAccount, account.id) is None
    assert db.execute(select(Credential).where(Credential.bank_account_id == account.id)).scalars().first() is None
    assert db.execute(select(CollectionTask).where(CollectionTask.account_id == account.id)).scalars().first() is None
    log = db.execute(select(AuditLog).where(AuditLog.action == "account_delete").order_by(AuditLog.id.desc())).scalars().first()
    assert log is not None
    assert log.detail["account_no"] == "DEL0001"


def test_delete_account_with_statement_and_task(db):
    account = _account(db, "DEL0002")
    db.add(
        Statement(
            account_id=account.id,
            txn_date=dt.date(2026, 1, 2),
            direction="credit",
            amount=Decimal("100.00"),
            dedupe_hash="delete-statement-1",
        )
    )
    db.add(
        CollectionTask(
            account_id=account.id,
            type="statements",
            start_date=dt.date(2026, 1, 1),
            end_date=dt.date(2026, 1, 31),
            status="failed",
            idempotency_key="delete-test-2",
        )
    )
    db.commit()
    assert account_stats(db, account.id)["has_data"] is True

    result = delete_account(db, account.id, "DEL0002")
    assert result["deleted"]["statements"] == 1
    assert db.get(BankAccount, account.id) is None
    assert db.execute(select(Statement).where(Statement.account_id == account.id)).scalars().first() is None


def test_active_task_blocks_delete(db):
    account = _account(db, "DEL0003")
    db.add(
        CollectionTask(
            account_id=account.id,
            type="statements",
            start_date=dt.date(2026, 1, 1),
            end_date=dt.date(2026, 1, 31),
            status="running",
            idempotency_key="delete-test-3",
        )
    )
    db.commit()
    with pytest.raises(AccountAdminError, match="正在执行"):
        delete_account(db, account.id, "DEL0003")
    assert db.get(BankAccount, account.id) is not None


def test_toggle_collection(db):
    account = _account(db, "DEL0004", enabled=True)
    assert toggle_collection(db, account.id) is False
    db.expire_all()
    assert db.get(BankAccount, account.id).collect_enabled is False
    assert toggle_collection(db, account.id) is True
    db.expire_all()
    assert db.get(BankAccount, account.id).collect_enabled is True
