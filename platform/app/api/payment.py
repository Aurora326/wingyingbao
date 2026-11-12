from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import CashierScope, PayeeAccount, PaymentCollectTask, PaymentDoc, PaymentSourceAccount
from app.services.payment import (
    PaymentError,
    confirm_accounts,
    export_draft,
    import_payment_excel,
    review_doc,
)
from app.services.payment_collect import (
    PaymentCollectError,
    enqueue_payment_collect,
    run_payment_collect,
    task_dict,
)

router = APIRouter(prefix="/api/payment", tags=["payment"])


class ConfirmIn(BaseModel):
    payer_account_id: int
    payee_account_id: int
    reason: str = ""


class ReviewIn(BaseModel):
    action: str
    opinion: str = ""


class DraftIn(BaseModel):
    doc_ids: list[int]


class CollectIn(BaseModel):
    source: str = "mingyuan"
    limit: int = 5
    limit_nocontract: int = -1
    lookback_days: int = 0
    since: str = ""


def _operator(request: Request) -> str:
    user = getattr(request.state, "user", None)
    return getattr(user, "username", "") or "system"


def _allowed_book_sets(db: Session, request: Request) -> set[int] | None:
    user = getattr(request.state, "user", None)
    if user is None or getattr(user, "role", "") == "admin":
        return None
    book_sets = db.execute(
        select(CashierScope.book_set_id).where(
            CashierScope.user_id == user.id,
            CashierScope.status == "active",
        )
    ).scalars().all()
    return set(book_sets) if book_sets else None


def _doc_dict(doc: PaymentDoc) -> dict:
    return {
        "id": doc.id,
        "apply_no": doc.apply_no,
        "subject": doc.subject,
        "applicant": doc.applicant,
        "department": doc.department,
        "apply_date": doc.apply_date.isoformat() if doc.apply_date else "",
        "approval_type": doc.approval_type,
        "contract_name": doc.contract_name,
        "contract_no": doc.contract_no,
        "project_name": doc.project_name,
        "payee_name": doc.payee_name,
        "payee_account_no": doc.payee_account_no,
        "payee_bank": doc.payee_bank,
        "payee_branch": doc.payee_branch,
        "payer_name": doc.payer_name,
        "payer_account_no": doc.payer_account_no,
        "payer_bank": doc.payer_bank,
        "payer_account_id": doc.payer_account_id,
        "payer_sub_account_id": doc.payer_sub_account_id,
        "payee_account_id": doc.payee_account_id,
        "amount": str(doc.amount),
        "paid_amount": str(doc.paid_amount) if doc.paid_amount is not None else "",
        "currency": doc.currency,
        "approval_status": doc.approval_status,
        "payment_status": doc.payment_status,
        "approval_node": doc.approval_node,
        "approval_handler": doc.approval_handler,
        "description": doc.description,
        "approval_steps": doc.approval_steps or [],
        "status": doc.status,
        "confirmed_by": doc.confirmed_by,
        "confirmed_at": doc.confirmed_at.isoformat() if doc.confirmed_at else "",
        "reviewed_by": doc.reviewed_by,
        "reviewed_at": doc.reviewed_at.isoformat() if doc.reviewed_at else "",
        "reject_reason": doc.reject_reason,
        "drafted_by": doc.drafted_by,
        "drafted_at": doc.drafted_at.isoformat() if doc.drafted_at else "",
        "draft_ref": doc.draft_ref,
    }


@router.post("/import")
async def import_excel(
    file: UploadFile = File(...),
    force: bool = Form(False),
    db: Session = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="请选择 Excel 文件")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".xlsx", ".xlsm"):
        raise HTTPException(status_code=400, detail="只支持 .xlsx 文件")
    upload_dir = settings.data_dir / "payment_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / f"payment_{dt.datetime.now():%Y%m%d_%H%M%S}{suffix}"
    with target.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    try:
        return import_payment_excel(db, target, force=force)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/docs")
def list_docs(
    request: Request,
    status: str | None = None,
    q: str | None = None,
    book_set_id: int | None = None,
    page: int = 1,
    size: int = 50,
    db: Session = Depends(get_db),
):
    query = select(PaymentDoc)
    if status:
        query = query.where(PaymentDoc.status == status)
    if book_set_id:
        query = query.where(PaymentDoc.book_set_id == book_set_id)
    scope = _allowed_book_sets(db, request)
    if scope is not None:
        query = query.where(PaymentDoc.book_set_id.in_(scope))
    if q:
        pattern = f"%{q}%"
        query = query.where(
            or_(
                PaymentDoc.apply_no.like(pattern),
                PaymentDoc.subject.like(pattern),
                PaymentDoc.payee_name.like(pattern),
                PaymentDoc.payer_name.like(pattern),
                PaymentDoc.project_name.like(pattern),
            )
        )
    total = db.execute(query.with_only_columns(func.count(PaymentDoc.id)).order_by(None)).scalar_one()
    rows = db.execute(
        query.order_by(PaymentDoc.apply_date.desc().nullslast(), PaymentDoc.id.desc())
        .offset(max(page - 1, 0) * size)
        .limit(min(size, 200))
    ).scalars().all()
    return {"total": total, "page": page, "size": size, "items": [_doc_dict(doc) for doc in rows]}


@router.get("/docs/{doc_id}")
def get_doc(doc_id: int, request: Request, db: Session = Depends(get_db)):
    doc = db.get(PaymentDoc, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="付款单据不存在")
    scope = _allowed_book_sets(db, request)
    if scope is not None and doc.book_set_id not in scope:
        raise HTTPException(status_code=403, detail="无权查看该账套的单据")
    return _doc_dict(doc)


@router.post("/docs/{doc_id}/confirm")
def confirm(
    doc_id: int,
    payload: ConfirmIn,
    request: Request,
    db: Session = Depends(get_db),
):
    doc = db.get(PaymentDoc, doc_id)
    scope = _allowed_book_sets(db, request)
    if doc is None:
        raise HTTPException(status_code=404, detail="付款单据不存在")
    if scope is not None and doc.book_set_id not in scope:
        raise HTTPException(status_code=403, detail="无权操作该账套的单据")
    try:
        doc = confirm_accounts(
            db,
            doc_id,
            payload.payer_account_id,
            payload.payee_account_id,
            reason=payload.reason,
            operator=_operator(request),
        )
    except PaymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _doc_dict(doc)


@router.post("/docs/{doc_id}/review")
def review(
    doc_id: int,
    payload: ReviewIn,
    request: Request,
    db: Session = Depends(get_db),
):
    doc = db.get(PaymentDoc, doc_id)
    scope = _allowed_book_sets(db, request)
    if doc is None:
        raise HTTPException(status_code=404, detail="付款单据不存在")
    if scope is not None and doc.book_set_id not in scope:
        raise HTTPException(status_code=403, detail="无权操作该账套的单据")
    try:
        doc = review_doc(db, doc_id, payload.action, payload.opinion, reviewer=_operator(request))
    except PaymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _doc_dict(doc)


@router.post("/drafts/export")
def drafts_export(payload: DraftIn, request: Request, db: Session = Depends(get_db)):
    scope = _allowed_book_sets(db, request)
    if scope is not None:
        for doc_id in payload.doc_ids:
            doc = db.get(PaymentDoc, doc_id)
            if doc is None or doc.book_set_id not in scope:
                raise HTTPException(status_code=403, detail="存在无权制单的单据")
    try:
        path, count = export_draft(db, payload.doc_ids, operator=_operator(request))
    except PaymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"path": str(path), "count": count}


@router.get("/payees")
def list_payees(q: str | None = None, limit: int = 50, db: Session = Depends(get_db)):
    query = select(PayeeAccount).where(PayeeAccount.status == "active")
    if q:
        pattern = f"%{q}%"
        query = query.where(
            or_(
                PayeeAccount.payee_name.like(pattern),
                PayeeAccount.account_no.like(pattern),
                PayeeAccount.bank_name.like(pattern),
            )
        )
    rows = db.execute(query.order_by(PayeeAccount.id.desc()).limit(min(limit, 200))).scalars().all()
    return {
        "items": [
            {
                "id": row.id,
                "payee_name": row.payee_name,
                "account_no": row.account_no,
                "bank_name": row.bank_name,
                "status": row.status,
                "source": row.source,
            }
            for row in rows
        ]
    }


@router.post("/collect")
def collect(payload: CollectIn, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    try:
        task = enqueue_payment_collect(
            db,
            limit=payload.limit,
            limit_nocontract=payload.limit_nocontract,
            lookback_days=payload.lookback_days,
            since=payload.since,
            created_by=_operator(request),
            source=payload.source,
        )
    except PaymentCollectError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    account = db.execute(
        select(PaymentSourceAccount).where(PaymentSourceAccount.source == payload.source)
    ).scalars().first()
    if account is not None:
        if payload.source == "oa":
            account.collect_days = max(1, min(int(payload.lookback_days or 0), 365))
        else:
            account.collect_limit = max(0, int(payload.limit))
            account.collect_nocontract = max(0, int(payload.limit_nocontract))
        db.commit()
    if settings.payment_collect_mode == "local":
        background_tasks.add_task(run_payment_collect, task.id)
    return task_dict(task)


@router.get("/collect/tasks")
def collect_tasks(limit: int = 20, db: Session = Depends(get_db)):
    rows = db.execute(
        select(PaymentCollectTask).order_by(PaymentCollectTask.id.desc()).limit(min(limit, 100))
    ).scalars().all()
    return {"items": [task_dict(row) for row in rows]}


@router.get("/collect/tasks/{task_id}")
def collect_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(PaymentCollectTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="采集任务不存在")
    return task_dict(task)


@router.get("/summary")
def summary(request: Request, db: Session = Depends(get_db)):
    query = select(PaymentDoc.status, func.count(PaymentDoc.id))
    scope = _allowed_book_sets(db, request)
    if scope is not None:
        query = query.where(PaymentDoc.book_set_id.in_(scope))
    rows = db.execute(query.group_by(PaymentDoc.status)).all()
    counts = {status: count for status, count in rows}
    total_query = select(func.count(PaymentDoc.id))
    if scope is not None:
        total_query = total_query.where(PaymentDoc.book_set_id.in_(scope))
    total = db.execute(total_query).scalar_one()
    return {"total": total, "by_status": counts}
