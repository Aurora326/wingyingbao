from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.models import BankAccount, Credential, Enterprise, PaymentDoc, PaymentDraftTask, UkeyDevice
from app.schemas import AgentTaskResult
from app.services import security
from app.services.payment_draft import (
    TASK_REVIEW,
    TASK_SUCCEEDED,
    apply_draft_task_result,
    claim_draft_task,
    create_batch,
    mark_task_done,
)


def _seed(db, account_no: str, apply_no: str) -> tuple[BankAccount, PaymentDoc]:
    enterprise = Enterprise(name=f"制单测试-{account_no}")
    db.add(enterprise)
    db.flush()
    device = UkeyDevice(
        name="建设银行 · 3号口",
        bank_code="ccb",
        backend="mb",
        agent_code="draft-agent-test",
        gf3_cabinet=1533,
        gf3_port=3,
        status="online",
        extra={"ter_id": "068330001533"},
    )
    db.add(device)
    db.flush()
    account = BankAccount(
        enterprise_id=enterprise.id,
        bank_code="ccb",
        account_no=account_no,
        account_name="制单测试账户",
        payment_enabled=True,
        ukey_device_id=device.id,
    )
    db.add(account)
    db.flush()
    db.add(
        Credential(
            bank_account_id=account.id,
            username_enc=security.encrypt("user"),
            password_enc=security.encrypt("pass"),
            pin_enc=security.encrypt("123456"),
        )
    )
    doc = PaymentDoc(
        enterprise_id=enterprise.id,
        apply_no=apply_no,
        payer_account_id=account.id,
        payer_name=account.account_name,
        payer_account_no=account_no,
        payee_name="测试收款方",
        payee_account_no="6222000099998888",
        payee_bank="中国建设银行上海测试支行",
        amount=Decimal("100.00"),
        status="ready",
    )
    db.add(doc)
    db.commit()
    return account, doc


def test_claim_and_review_then_done(db):
    account, doc = _seed(db, "31050100000000000001", "DRAFT-A-1")
    batch = create_batch(db, [doc.id], operator="tester")
    task = db.execute(
        select(PaymentDraftTask).where(PaymentDraftTask.batch_id == batch.id)
    ).scalars().first()
    assert task.status == "queued"

    payload = claim_draft_task(db, "draft-agent-test")
    assert payload is not None
    assert payload["task_id"] == task.id
    assert payload["credentials"]["pin"] == "123456"
    assert payload["device"]["ter_id"] == "068330001533"
    assert payload["file_url"].endswith(f"/draft-tasks/{task.id}/file")

    result = AgentTaskResult(
        agent_code="draft-agent-test",
        attempt=1,
        exit_code=0,
        log_text="批量导入准备完成，已停在下一步前",
        result={"ok": True},
    )
    out = apply_draft_task_result(db, task.id, result, "draft-agent-test")
    assert out["status"] == TASK_REVIEW
    db.expire_all()
    row = db.get(PaymentDraftTask, task.id)
    assert row.status == TASK_REVIEW
    assert row.log_text
    doc_row = db.get(PaymentDoc, doc.id)
    assert doc_row.status == "drafting"

    mark_task_done(db, task.id, operator="tester")
    db.expire_all()
    assert db.get(PaymentDraftTask, task.id).status == TASK_SUCCEEDED
    assert db.get(PaymentDoc, doc.id).status == "drafted"


def test_failure_releases_docs(db):
    account, doc = _seed(db, "31050100000000000002", "DRAFT-B-1")
    batch = create_batch(db, [doc.id], operator="tester")
    task = db.execute(
        select(PaymentDraftTask).where(PaymentDraftTask.batch_id == batch.id)
    ).scalars().first()
    payload = claim_draft_task(db, "draft-agent-test")
    assert payload["task_id"] == task.id

    result = AgentTaskResult(
        agent_code="draft-agent-test",
        attempt=1,
        exit_code=1,
        log_text="登录失败",
        result={"ok": False, "error_code": "LOGIN_FAILED", "message": "登录失败"},
    )
    out = apply_draft_task_result(db, task.id, result, "draft-agent-test")
    assert out["status"] == "failed"
    db.expire_all()
    row = db.get(PaymentDraftTask, task.id)
    assert row.error_code == "LOGIN_FAILED"
    assert db.get(PaymentDoc, doc.id).status == "ready"
