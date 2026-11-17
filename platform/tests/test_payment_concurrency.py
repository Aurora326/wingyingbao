from __future__ import annotations

import datetime as dt
import threading
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select

from openpyxl import load_workbook

from app.db import SessionLocal
from app.models import (
    AuditLog,
    BankAccount,
    CollectionTask,
    Enterprise,
    PayeeAccount,
    PaymentDoc,
    PaymentDraftBatch,
    PaymentDraftTask,
)
from app.services.agent_tasks import claim_agent_task
from app.services.payment import PaymentError, confirm_accounts, export_draft
from app.services.payment_draft import DraftError, claim_draft_task, create_batch


def _account(db, suffix: str) -> BankAccount:
    enterprise = Enterprise(name=f"并发测试企业{suffix}")
    db.add(enterprise)
    db.flush()
    account = BankAccount(
        enterprise_id=enterprise.id,
        bank_code="citic",
        account_no=f"62{suffix}",
        account_name=f"并发账户{suffix}",
    )
    db.add(account)
    db.commit()
    return account


def test_claim_agent_task_only_once(db):
    stamp = int(dt.datetime.now().timestamp() * 1000)
    account = _account(db, f"1{stamp}")
    task = CollectionTask(
        account_id=account.id,
        type="statement",
        start_date=dt.date.today(),
        end_date=dt.date.today(),
        status="queued",
        agent_code=f"agent-x-{stamp}",
        idempotency_key=f"claim-{stamp}",
    )
    db.add(task)
    db.commit()

    barrier = threading.Barrier(2)
    results: list[dict | None] = []
    lock = threading.Lock()

    def worker() -> None:
        with SessionLocal() as session:
            barrier.wait()
            result = claim_agent_task(session, f"agent-x-{stamp}")
            with lock:
                results.append(result)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    claimed_ids = [item["task_id"] for item in results if item]
    assert claimed_ids.count(task.id) == 1

    db.execute(delete(CollectionTask).where(CollectionTask.id == task.id))
    db.commit()


def test_claim_draft_task_only_once(db):
    stamp = int(dt.datetime.now().timestamp() * 1000)
    batch = PaymentDraftBatch(
        batch_no=f"DTEST-{stamp}",
        status="running",
        doc_count=1,
        total_amount=Decimal("0"),
    )
    db.add(batch)
    db.flush()
    task = PaymentDraftTask(
        batch_id=batch.id,
        status="queued",
        agent_code=f"agent-y-{stamp}",
        payer_account_no="TEST",
        doc_count=0,
    )
    db.add(task)
    db.commit()

    barrier = threading.Barrier(2)
    results: list[dict | None] = []
    lock = threading.Lock()

    def worker() -> None:
        with SessionLocal() as session:
            barrier.wait()
            result = claim_draft_task(session, f"agent-y-{stamp}")
            with lock:
                results.append(result)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    claimed_ids = [item["task_id"] for item in results if item]
    assert claimed_ids.count(task.id) == 1

    db.execute(delete(PaymentDraftTask).where(PaymentDraftTask.batch_id == batch.id))
    db.execute(delete(PaymentDraftBatch).where(PaymentDraftBatch.id == batch.id))
    db.commit()


def test_confirm_twice_rejected_and_audited(db):
    stamp = int(dt.datetime.now().timestamp() * 1000)
    account = _account(db, f"2{stamp}")
    payee = PayeeAccount(payee_name="并发收款单位", account_no=f"9{stamp}", bank_name="中信银行")
    db.add(payee)
    db.commit()
    doc = PaymentDoc(
        apply_no=f"CONF-{stamp}",
        status="pending_confirm",
        amount=Decimal("100.00"),
        currency="CNY",
        payer_name=account.account_name,
        payer_account_no=account.account_no,
    )
    db.add(doc)
    db.commit()

    confirm_accounts(db, doc.id, account.id, payee.id, operator="tester")
    with pytest.raises(PaymentError):
        confirm_accounts(db, doc.id, account.id, payee.id, operator="tester")

    actions = {
        log.action
        for log in db.execute(select(AuditLog).where(AuditLog.target_id == str(doc.id))).scalars()
    }
    assert "payment_confirm" in actions


def test_create_batch_twice_single_batch(db):
    stamp = int(dt.datetime.now().timestamp() * 1000)
    account = _account(db, f"3{stamp}")
    doc = PaymentDoc(
        apply_no=f"BATCH-{stamp}",
        status="ready",
        amount=Decimal("50.00"),
        currency="CNY",
        payer_account_id=account.id,
        payer_account_no=account.account_no,
        payer_name=account.account_name,
        payee_name="并发收款单位",
        payee_account_no="80001",
    )
    db.add(doc)
    db.commit()

    before = db.execute(select(func.count()).select_from(PaymentDraftBatch)).scalar_one()
    batch = create_batch(db, [doc.id], operator="tester")
    with pytest.raises(DraftError):
        create_batch(db, [doc.id], operator="tester")
    after = db.execute(select(func.count()).select_from(PaymentDraftBatch)).scalar_one()

    assert after == before + 1

    db.execute(delete(PaymentDraftTask).where(PaymentDraftTask.batch_id == batch.id))
    db.execute(delete(PaymentDraftBatch).where(PaymentDraftBatch.id == batch.id))
    db.commit()


def test_export_draft_keeps_decimal_precision(db):
    stamp = int(dt.datetime.now().timestamp() * 1000)
    account = _account(db, f"4{stamp}")
    amount = Decimal("0.1") + Decimal("0.2")
    assert amount == Decimal("0.30")
    doc = PaymentDoc(
        apply_no=f"EXPORT-{stamp}",
        status="ready",
        amount=amount,
        currency="CNY",
        payer_account_id=account.id,
        payer_account_no=account.account_no,
        payer_name=account.account_name,
        payee_name="精度测试收款单位",
        payee_account_no="70001",
    )
    db.add(doc)
    db.commit()

    path, count = export_draft(db, [doc.id], operator="tester")
    assert count == 1
    cell = load_workbook(path, data_only=True).active.cell(row=2, column=8)
    assert abs(cell.value - 0.30) < 1e-9
    assert cell.number_format == "#,##0.00"
