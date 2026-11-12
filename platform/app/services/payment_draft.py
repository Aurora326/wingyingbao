"""制单批次：把"可制单"的单据按付款账户拆成任务，每个账户生成一份批量文件。

第一期是半自动：平台生成文件，出纳下载后到网银导入，回来点「已制单」。
后续接银行自动化时，只需把"下载+导入"这一步换成采集机领任务执行。
"""
from __future__ import annotations

import re

import datetime as dt
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    BankAccount,
    Credential,
    PaymentDoc,
    PaymentDraftBatch,
    PaymentDraftTask,
    UkeyDevice,
)
from app.services import security

STATUS_DRAFTING = "drafting"

TASK_QUEUED = "queued"
TASK_RUNNING = "running"
TASK_SUCCEEDED = "succeeded"
TASK_REVIEW = "review"
TASK_FAILED = "failed"

BATCH_RUNNING = "running"
BATCH_SUCCEEDED = "succeeded"
BATCH_PARTIAL = "partial"
BATCH_FAILED = "failed"

DRAFT_HEADERS = [
    "序号",
    "申请编号",
    "付款单位",
    "付款账户",
    "收款单位",
    "收款账号",
    "收款开户行",
    "金额",
    "币种",
    "用途",
    "项目",
]


class DraftError(Exception):
    def __init__(self, message: str, problems: list[dict] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.problems = problems or []


def _check_doc(doc: PaymentDoc) -> str:
    """返回这条单据不能制单的原因，空字符串表示可以。"""
    if doc.status == STATUS_DRAFTING:
        return "已在制单中"
    if doc.status != "ready":
        return "状态不是可制单"
    if not doc.payer_account_id:
        return "缺少付款账户，无法确定所属银行"
    if doc.amount is None or Decimal(doc.amount) <= 0:
        return "金额不正确"
    if not (doc.payee_account_no or "").strip():
        return "缺少收款账号"
    return ""


def load_docs(db: Session, doc_ids: list[int]) -> tuple[list[PaymentDoc], list[dict]]:
    """按传入顺序取出单据，并把不能制单的挑出来。"""
    wanted = list(dict.fromkeys(int(i) for i in doc_ids))
    if not wanted:
        raise DraftError("请先选择要制单的单据")
    docs = db.execute(select(PaymentDoc).where(PaymentDoc.id.in_(wanted))).scalars().all()
    by_id = {doc.id: doc for doc in docs}
    picked: list[PaymentDoc] = []
    problems: list[dict] = []
    for doc_id in wanted:
        doc = by_id.get(doc_id)
        if doc is None:
            problems.append({"id": doc_id, "apply_no": "", "reason": "单据不存在"})
            continue
        reason = _check_doc(doc)
        if reason:
            problems.append({"id": doc.id, "apply_no": doc.apply_no, "reason": reason})
            continue
        picked.append(doc)
    return picked, problems


def _group(docs: list[PaymentDoc]) -> dict[tuple[int, str], list[PaymentDoc]]:
    groups: dict[tuple[int, str], list[PaymentDoc]] = {}
    for doc in docs:
        key = (int(doc.payer_account_id or 0), doc.currency or "CNY")
        groups.setdefault(key, []).append(doc)
    return groups


def preview(db: Session, doc_ids: list[int]) -> dict:
    docs, problems = load_docs(db, doc_ids)
    groups = _group(docs)
    accounts = {
        account.id: account
        for account in db.execute(
            select(BankAccount).where(BankAccount.id.in_([key[0] for key in groups] or [0]))
        ).scalars().all()
    }
    rows = []
    for (account_id, currency), items in groups.items():
        account = accounts.get(account_id)
        rows.append(
            {
                "account_id": account_id,
                "account_no": account.account_no if account else "",
                "account_name": account.account_name if account else "",
                "bank_code": account.bank_code if account else "",
                "currency": currency,
                "count": len(items),
                "amount": float(sum((Decimal(doc.amount) for doc in items), Decimal("0"))),
            }
        )
    rows.sort(key=lambda row: row["amount"], reverse=True)
    return {
        "groups": rows,
        "problems": problems,
        "doc_ids": [doc.id for doc in docs],
        "count": len(docs),
        "amount": float(sum((Decimal(doc.amount) for doc in docs), Decimal("0"))),
    }


def _next_batch_no(db: Session) -> str:
    today = dt.date.today().strftime("%Y%m%d")
    prefix = f"D{today}-"
    used = db.execute(
        select(PaymentDraftBatch.batch_no).where(PaymentDraftBatch.batch_no.like(f"{prefix}%"))
    ).scalars().all()
    seq = 0
    for value in used:
        tail = value[len(prefix) :]
        if tail.isdigit():
            seq = max(seq, int(tail))
    return f"{prefix}{seq + 1:03d}"


def _write_file(task_dir: Path, task: PaymentDraftTask, docs: list[PaymentDoc]) -> Path:
    path = task_dir / f"{task.bank_code}_{task.payer_account_no}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "付款制单"
    ws.append(DRAFT_HEADERS)
    for index, doc in enumerate(docs, 1):
        ws.append(
            [
                index,
                doc.apply_no,
                doc.payer_name,
                doc.payer_account_no,
                doc.payee_name,
                doc.payee_account_no,
                doc.payee_bank,
                float(doc.amount or 0),
                doc.currency,
                doc.subject,
                doc.project_name,
            ]
        )
    ws.freeze_panes = "A2"
    wb.save(path)
    return path


def create_batch(db: Session, doc_ids: list[int], operator: str = "") -> PaymentDraftBatch:
    """按付款账户拆分并落库，单据进入制单中，同一批不会重复发起。"""
    docs, problems = load_docs(db, doc_ids)
    if problems:
        names = "、".join(item["apply_no"] or f"#{item['id']}" for item in problems[:5])
        raise DraftError(f"{len(problems)} 条单据不能制单：{names}", problems)
    if not docs:
        raise DraftError("没有可制单的单据")

    groups = _group(docs)
    batch = PaymentDraftBatch(
        batch_no=_next_batch_no(db),
        status=BATCH_RUNNING,
        doc_count=len(docs),
        total_amount=sum((Decimal(doc.amount) for doc in docs), Decimal("0")),
        created_by=operator,
    )
    db.add(batch)
    db.flush()

    batch_dir = settings.data_dir / "payment_drafts" / batch.batch_no
    batch_dir.mkdir(parents=True, exist_ok=True)
    accounts = {
        account.id: account
        for account in db.execute(
            select(BankAccount).where(BankAccount.id.in_([key[0] for key in groups] or [0]))
        ).scalars().all()
    }
    devices = {
        device.id: device
        for device in db.execute(
            select(UkeyDevice).where(
                UkeyDevice.id.in_(
                    [a.ukey_device_id for a in accounts.values() if a.ukey_device_id] or [0]
                )
            )
        ).scalars().all()
    }

    for (account_id, _currency), items in groups.items():
        account = accounts.get(account_id)
        device = devices.get(account.ukey_device_id) if account and account.ukey_device_id else None
        task = PaymentDraftTask(
            batch_id=batch.id,
            bank_code=account.bank_code if account else "",
            payer_account_id=account_id,
            payer_account_no=account.account_no if account else "",
            payer_name=account.account_name if account else "",
            ukey_device_id=device.id if device else None,
            agent_code=device.agent_code if device else "",
            status=TASK_QUEUED,
            doc_count=len(items),
            amount=sum((Decimal(doc.amount) for doc in items), Decimal("0")),
        )
        db.add(task)
        db.flush()
        task.file_path = str(_write_file(batch_dir, task, items))
        for doc in items:
            doc.status = STATUS_DRAFTING
            doc.draft_batch_id = batch.id
            doc.draft_task_id = task.id

    db.commit()
    return batch


def mark_task_done(db: Session, task_id: int, operator: str = "") -> PaymentDraftTask:
    task = db.get(PaymentDraftTask, task_id)
    if task is None:
        raise DraftError("制单任务不存在")
    if task.status == TASK_SUCCEEDED:
        return task
    if task.status == TASK_FAILED:
        raise DraftError("已失败的任务不能直接标记完成，请重新发起制单")

    now = dt.datetime.now()
    docs = db.execute(select(PaymentDoc).where(PaymentDoc.draft_task_id == task.id)).scalars().all()
    for doc in docs:
        doc.status = "drafted"
        doc.drafted_by = operator
        doc.drafted_at = now
        doc.draft_ref = task.file_path

    task.status = TASK_SUCCEEDED
    task.done_by = operator
    task.done_at = now
    _refresh_batch(db, task.batch_id)
    db.commit()
    return task


def fail_task(db: Session, task_id: int, message: str, release: bool = True) -> PaymentDraftTask:
    """任务失败。release=True 时把单据退回可制单，出纳可以改完再发一次。"""
    task = db.get(PaymentDraftTask, task_id)
    if task is None:
        raise DraftError("制单任务不存在")
    task.status = TASK_FAILED
    task.error_msg = (message or "制单失败")[:512]
    if release:
        docs = db.execute(
            select(PaymentDoc).where(PaymentDoc.draft_task_id == task.id)
        ).scalars().all()
        for doc in docs:
            doc.status = "ready"
            doc.draft_batch_id = None
            doc.draft_task_id = None
    _refresh_batch(db, task.batch_id)
    db.commit()
    return task


def cancel_task(db: Session, task_id: int, operator: str = "") -> PaymentDraftTask:
    """撤回：没制单完成的单据退回可制单。"""
    return fail_task(db, task_id, f"已由 {operator or 'someone'} 撤回", release=True)


def _refresh_batch(db: Session, batch_id: int) -> None:
    batch = db.get(PaymentDraftBatch, batch_id)
    if batch is None:
        return
    tasks = db.execute(
        select(PaymentDraftTask).where(PaymentDraftTask.batch_id == batch_id)
    ).scalars().all()
    done = sum(1 for task in tasks if task.status == TASK_SUCCEEDED)
    failed = sum(1 for task in tasks if task.status == TASK_FAILED)
    if done == len(tasks):
        batch.status = BATCH_SUCCEEDED
        batch.finished_at = dt.datetime.now()
    elif failed == len(tasks):
        batch.status = BATCH_FAILED
        batch.finished_at = dt.datetime.now()
    elif done + failed == len(tasks):
        batch.status = BATCH_PARTIAL
        batch.finished_at = dt.datetime.now()
    else:
        batch.status = BATCH_RUNNING
        batch.finished_at = None


LEASE_MINUTES = 30
_ANSI_RE = re.compile(r"\[[0-9;]*[A-Za-z]")


def _clean_log(text: str | None) -> str:
    return _ANSI_RE.sub("", text or "")[-20000:]


def _draft_payload(db: Session, task: PaymentDraftTask) -> dict:
    batch = db.get(PaymentDraftBatch, task.batch_id)
    account = db.get(BankAccount, task.payer_account_id) if task.payer_account_id else None
    credential = (
        db.execute(
            select(Credential).where(Credential.bank_account_id == task.payer_account_id)
        ).scalars().first()
        if task.payer_account_id
        else None
    )
    device = db.get(UkeyDevice, task.ukey_device_id) if task.ukey_device_id else None
    extra = (device.extra or {}) if device else {}
    return {
        "task_id": task.id,
        "batch_no": batch.batch_no if batch else "",
        "bank_code": task.bank_code,
        "payer_account_no": task.payer_account_no or (account.account_no if account else ""),
        "payer_name": task.payer_name or (account.account_name if account else ""),
        "doc_count": task.doc_count,
        "amount": float(task.amount or 0),
        "file_name": Path(task.file_path).name if task.file_path else "",
        "file_url": f"/api/agent/draft-tasks/{task.id}/file",
        "credentials": {
            "username": security.decrypt(credential.username_enc) if credential else "",
            "password": security.decrypt(credential.password_enc) if credential else "",
            "pin": security.decrypt(credential.pin_enc) if credential else "",
        },
        "device": {
            "device_id": device.id if device else 0,
            "backend": device.backend if device else "",
            "gf3_cabinet": device.gf3_cabinet if device else 0,
            "gf3_port": device.gf3_port if device else 0,
            "ter_id": str(extra.get("ter_id") or ""),
            "mb_user": str(extra.get("mb_user") or ""),
        },
        "attempt": task.attempts,
    }


def claim_draft_task(db: Session, agent_code: str) -> dict | None:
    """采集机领取一个制单任务，租约 30 分钟。"""
    now = dt.datetime.now()
    stale = db.execute(
        select(PaymentDraftTask).where(
            PaymentDraftTask.status == TASK_RUNNING,
            PaymentDraftTask.lease_until.is_not(None),
            PaymentDraftTask.lease_until < now,
        )
    ).scalars().all()
    for row in stale:
        row.status = TASK_QUEUED
        row.lease_until = None
        row.agent_code = ""
        row.error_code = "LEASE_EXPIRED"
        row.error_msg = "执行超时，已退回队列"

    task = db.execute(
        select(PaymentDraftTask)
        .where(
            PaymentDraftTask.status == TASK_QUEUED,
            (PaymentDraftTask.agent_code == agent_code) | (PaymentDraftTask.agent_code == ""),
        )
        .order_by(PaymentDraftTask.id)
        .limit(1)
    ).scalars().first()
    if task is None:
        db.commit()
        return None
    task.status = TASK_RUNNING
    task.agent_code = agent_code
    task.started_at = now
    task.lease_until = now + dt.timedelta(minutes=LEASE_MINUTES)
    task.attempts += 1
    task.error_code = ""
    task.error_msg = ""
    db.commit()
    return _draft_payload(db, task)


def apply_draft_task_result(db: Session, task_id: int, payload, agent_code: str) -> dict:
    """采集机回传制单结果：成功则单据转已制单，失败退回可制单。"""
    task = db.get(PaymentDraftTask, task_id)
    if task is None:
        raise DraftError("制单任务不存在")
    if task.agent_code and task.agent_code != agent_code:
        raise DraftError("任务不属于该 Agent")
    if task.status not in (TASK_QUEUED, TASK_RUNNING):
        raise DraftError(f"任务状态为 {task.status}，不能回传结果")

    result = payload.result or {}
    ok = payload.exit_code in (0, None) and bool(result.get("ok", True))
    log_text = _clean_log(payload.log_text)
    if ok:
        # save 模式：文件已导入并停在下一步，等待出纳在网银完成后再标记已制单
        task.status = TASK_REVIEW
        task.finished_at = dt.datetime.now()
        task.log_text = log_text
        task.bank_batch_no = str(result.get("bank_batch_no") or "")[:64]
        task.done_by = f"agent:{agent_code}"
        _refresh_batch(db, task.batch_id)
        db.commit()
    else:
        message = str(result.get("message") or "制单失败")
        fail_task(db, task.id, message, release=True)
        task = db.get(PaymentDraftTask, task_id)
        task.finished_at = dt.datetime.now()
        task.error_code = str(result.get("error_code") or "DRAFT_FAILED")[:32]
        task.log_text = log_text
        db.commit()
    return draft_task_dict(task)


def draft_task_dict(task: PaymentDraftTask) -> dict:
    return {
        "id": task.id,
        "batch_id": task.batch_id,
        "bank_code": task.bank_code,
        "payer_account_no": task.payer_account_no,
        "payer_name": task.payer_name,
        "agent_code": task.agent_code,
        "status": task.status,
        "doc_count": task.doc_count,
        "amount": float(task.amount or 0),
        "error_code": task.error_code,
        "error_msg": task.error_msg,
        "bank_batch_no": task.bank_batch_no,
        "log_text": task.log_text or "",
        "started_at": task.started_at.isoformat() if task.started_at else "",
        "finished_at": task.finished_at.isoformat() if task.finished_at else "",
        "done_at": task.done_at.isoformat() if task.done_at else "",
    }
