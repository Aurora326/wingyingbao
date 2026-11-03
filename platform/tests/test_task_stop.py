from __future__ import annotations

import datetime as dt

from app.api.tasks import stop_task
from app.models import BankAccount, Enterprise
from app.services.agent_tasks import get_agent_task_status
from app.services.executor import _finalize
from app.services.scheduler import enqueue_task


def _account(db, account_no: str) -> BankAccount:
    enterprise = Enterprise(name=f"停止测试-{account_no}")
    db.add(enterprise)
    db.flush()
    account = BankAccount(
        enterprise_id=enterprise.id,
        bank_code="mock",
        account_no=account_no,
    )
    db.add(account)
    db.commit()
    return account


def test_stop_queued_task_cancels_immediately(db):
    account = _account(db, "STOP-0001")
    task = enqueue_task(
        db, account, "statements", dt.date(2026, 11, 1), dt.date(2026, 11, 2)
    )

    result = stop_task(task.id, db)
    assert result["status"] == "cancelled"
    assert result["cancel_requested"] is True

    db.expire_all()
    stopped = db.get(type(task), task.id)
    assert stopped.status == "cancelled"
    assert stopped.error_code == "CANCELLED"
    assert stopped.cancel_requested is True
    assert stopped.finished_at is not None


def test_stop_leased_task_requests_agent_and_finalizes_cancelled(db):
    account = _account(db, "STOP-0002")
    task = enqueue_task(
        db, account, "receipts", dt.date(2026, 11, 1), dt.date(2026, 11, 2)
    )
    task.status = "leased"
    task.attempts = 1
    db.commit()

    result = stop_task(task.id, db)
    assert result["status"] == "leased"
    assert result["action"] == "stopping"

    status = get_agent_task_status(db, task.id, task.agent_code)
    assert status["cancel_requested"] is True

    _finalize(db, task, {"error_code": "CANCELLED", "message": "用户停止任务"}, 130, "stop", 10)
    db.expire_all()
    stopped = db.get(type(task), task.id)
    assert stopped.status == "cancelled"
    assert stopped.error_code == "CANCELLED"
    assert stopped.finished_at is not None
