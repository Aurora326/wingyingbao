from __future__ import annotations

from sqlalchemy import select

from app.models import AuditLog, BankAccount, Enterprise
from app.services.audit import list_changes, snapshot, write_changes


def _account(db, suffix: str) -> BankAccount:
    enterprise = Enterprise(name="变更留痕测试单位")
    db.add(enterprise)
    db.flush()
    account = BankAccount(
        enterprise_id=enterprise.id,
        bank_code="ccb",
        account_no=f"TRACE-{suffix}",
        account_name="旧名称",
    )
    db.add(account)
    db.flush()
    return account


def test_write_changes_records_field_diff(db):
    account = _account(db, "DIFF")
    before = snapshot(account, "bank_account")
    account.account_name = "新名称"

    written = write_changes(
        db,
        account,
        before,
        actor="tester",
        action="account_update",
        target_type="bank_account",
        reason="改名",
    )
    db.flush()

    assert written
    log = db.execute(
        select(AuditLog).where(
            AuditLog.action == "account_update",
            AuditLog.target_type == "bank_account",
            AuditLog.target_id == str(account.id),
        )
    ).scalars().one()
    assert log.detail["changes"]["account_name"] == {"from": "旧名称", "to": "新名称"}
    assert log.detail["reason"] == "改名"
    assert log.detail["source"] == "manual"
    assert log.actor == "tester"


def test_write_changes_skips_when_unchanged(db):
    account = _account(db, "NOOP")
    before = snapshot(account, "bank_account")

    written = write_changes(
        db,
        account,
        before,
        actor="tester",
        action="account_update",
        target_type="bank_account",
    )
    db.flush()

    assert not written
    count = db.execute(
        select(AuditLog).where(
            AuditLog.action == "account_update",
            AuditLog.target_id == str(account.id),
        )
    ).scalars().all()
    assert count == []


def test_list_changes_filters_by_target(db):
    account = _account(db, "LIST")
    before = snapshot(account, "bank_account")
    account.account_name = "另一个名称"
    write_changes(
        db,
        account,
        before,
        actor="tester",
        action="account_update",
        target_type="bank_account",
    )
    db.flush()

    result = list_changes(db, "bank_account", account.id)
    assert result["total"] == 1
    assert result["items"][0].target_id == str(account.id)
    assert list_changes(db, "bank_account", account.id + 999)["total"] == 0
