from __future__ import annotations

from app.api.accounts import collect_now, update_account
from app.models import BankAccount, Enterprise
from app.schemas import AccountUpdate
from app.services.scheduler import task_types_for


def _account(db, account_no: str, mode: str) -> BankAccount:
    enterprise = Enterprise(name=f"采集模式-{account_no}")
    db.add(enterprise)
    db.flush()
    account = BankAccount(
        enterprise_id=enterprise.id,
        bank_code="mock",
        account_no=account_no,
        collect_mode=mode,
    )
    db.add(account)
    db.commit()
    return account


def test_task_types_for_mode():
    assert task_types_for("statements") == ("statements",)
    assert task_types_for("receipts") == ("receipts",)
    assert task_types_for("both") == ("statements", "receipts")
    assert task_types_for(None) == ("statements", "receipts")
    assert task_types_for("unknown") == ("statements", "receipts")


def test_collect_now_respects_account_mode_and_override(db):
    account = _account(db, "MODE-0001", "receipts")

    result = collect_now(account.id, db=db)
    assert [task["type"] for task in result["tasks"]] == ["receipts"]

    result = collect_now(account.id, task_type="statements", db=db)
    assert [task["type"] for task in result["tasks"]] == ["statements"]

    result = collect_now(account.id, task_type="both", db=db)
    assert [task["type"] for task in result["tasks"]] == ["statements", "receipts"]


def test_update_account_changes_collect_mode(db):
    account = _account(db, "MODE-0002", "both")

    update_account(account.id, AccountUpdate(collect_mode="statements"), db)
    db.expire_all()
    assert db.get(BankAccount, account.id).collect_mode == "statements"
