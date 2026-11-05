from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import AppUser, BankAccount, BookSet, CashierScope, Enterprise, PayeeAccount, SubAccount
from app.services.account_master import AccountMasterError, account_master_summary, import_account_master
from app.services.payment_collect import get_source_credentials, save_source_credentials
from app.services.banks import BANK_NAMES
from app.services.payee_admin import (
    PayeeError,
    change_dict,
    create_payee,
    list_changes,
    list_payees,
    payee_dict,
    reject_change,
    request_change,
    approve_change,
)

router = APIRouter(prefix="/api/payment", tags=["payment-settings"])


class CashierIn(BaseModel):
    user_id: int
    book_set_id: int
    role: str = "cashier"


class PayeeIn(BaseModel):
    payee_name: str
    account_no: str
    bank_name: str = ""
    branch_name: str = ""
    cnaps_code: str = ""
    currency: str = "CNY"


class SourceAccountIn(BaseModel):
    username: str
    password: str = ""


class PayeeChangeIn(BaseModel):
    payee_name: str | None = None
    account_no: str | None = None
    bank_name: str | None = None
    branch_name: str | None = None
    cnaps_code: str | None = None
    currency: str | None = None
    status: str | None = None
    reason: str = ""


def _operator(request: Request) -> str:
    user = getattr(request.state, "user", None)
    return getattr(user, "username", "") or "system"


@router.get("/source-account")
def source_account(db: Session = Depends(get_db)):
    username, password = get_source_credentials(db)
    return {"username": username, "configured": bool(username and password)}


@router.post("/source-account")
def save_source_account(payload: SourceAccountIn, db: Session = Depends(get_db)):
    try:
        save_source_credentials(db, payload.username.strip(), payload.password)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    username, password = get_source_credentials(db)
    return {"username": username, "configured": bool(username and password)}


@router.post("/accounts/import-master")
async def import_master(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename or Path(file.filename).suffix.lower() not in (".xlsx", ".xlsm"):
        raise HTTPException(status_code=400, detail="请选择账户主数据 Excel")
    upload_dir = settings.data_dir / "payment_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / f"accounts_{dt.datetime.now():%Y%m%d_%H%M%S}.xlsx"
    with target.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    try:
        return import_account_master(db, target)
    except AccountMasterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/accounts/master")
def master_accounts(db: Session = Depends(get_db)):
    accounts = db.execute(select(BankAccount).order_by(BankAccount.id)).scalars().all()
    subs = db.execute(select(SubAccount).order_by(SubAccount.parent_account_id, SubAccount.sub_no)).scalars().all()
    enterprises = {e.id: e.name for e in db.execute(select(Enterprise)).scalars().all()}
    books = {b.enterprise_id: b for b in db.execute(select(BookSet)).scalars().all()}
    sub_count = {}
    for sub in subs:
        sub_count[sub.parent_account_id] = sub_count.get(sub.parent_account_id, 0) + 1
    return {
        "summary": account_master_summary(db),
        "items": [
            {
                "id": a.id,
                "enterprise_id": a.enterprise_id,
                "enterprise_name": enterprises.get(a.enterprise_id, ""),
                "book_set_id": books.get(a.enterprise_id).id if books.get(a.enterprise_id) else None,
                "book_set_name": books.get(a.enterprise_id).name if books.get(a.enterprise_id) else "",
                "bank_code": a.bank_code,
                "bank_name": BANK_NAMES.get(a.bank_code, a.bank_code),
                "account_no": a.account_no,
                "account_name": a.account_name,
                "account_type": a.account_type,
                "payment_enabled": a.payment_enabled,
                "auto_voucher_enabled": a.auto_voucher_enabled,
                "has_ukey": a.has_ukey,
                "has_sub_accounts": a.has_sub_accounts,
                "parent_account_id": a.parent_account_id,
                "usage_tag": a.usage_tag,
                "status": a.account_status,
                "sub_count": sub_count.get(a.id, 0),
            }
            for a in accounts
        ],
        "sub_accounts": [
            {
                "id": s.id,
                "parent_account_id": s.parent_account_id,
                "sub_no": s.sub_no,
                "ledger_name": s.ledger_name,
                "project_name": s.project_name,
                "currency": s.currency,
                "status": s.status,
            }
            for s in subs
        ],
    }


@router.get("/cashiers")
def cashiers(db: Session = Depends(get_db)):
    rows = db.execute(select(CashierScope).order_by(CashierScope.id.desc())).scalars().all()
    users = {u.id: u for u in db.execute(select(AppUser)).scalars().all()}
    books = {b.id: b for b in db.execute(select(BookSet)).scalars().all()}
    return {
        "items": [
            {
                "id": row.id,
                "user_id": row.user_id,
                "username": users[row.user_id].username if row.user_id in users else "",
                "book_set_id": row.book_set_id,
                "book_set_name": books[row.book_set_id].name if row.book_set_id in books else "",
                "role": row.role,
                "status": row.status,
            }
            for row in rows
        ],
        "users": [{"id": u.id, "username": u.username, "role": u.role} for u in users.values()],
        "book_sets": [{"id": b.id, "name": b.name} for b in books.values()],
    }


@router.post("/cashiers")
def create_cashier(payload: CashierIn, db: Session = Depends(get_db)):
    if db.get(AppUser, payload.user_id) is None:
        raise HTTPException(status_code=400, detail="用户不存在")
    if db.get(BookSet, payload.book_set_id) is None:
        raise HTTPException(status_code=400, detail="账套不存在")
    scope = CashierScope(user_id=payload.user_id, book_set_id=payload.book_set_id, role=payload.role)
    db.add(scope)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="该用户已分配该账套") from exc
    return {"id": scope.id}


@router.delete("/cashiers/{scope_id}")
def delete_cashier(scope_id: int, db: Session = Depends(get_db)):
    scope = db.get(CashierScope, scope_id)
    if scope is None:
        raise HTTPException(status_code=404, detail="分配记录不存在")
    db.delete(scope)
    db.commit()
    return {"ok": True}


@router.get("/payees/all")
def all_payees(q: str = "", status: str = "", db: Session = Depends(get_db)):
    return {"items": [payee_dict(p) for p in list_payees(db, q, status)]}


@router.post("/payees")
def create_payee_api(payload: PayeeIn, request: Request, db: Session = Depends(get_db)):
    try:
        payee = create_payee(db, payload.model_dump(), operator=_operator(request))
    except PayeeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return payee_dict(payee)


@router.post("/payees/{payee_id}/change")
def request_payee_change(
    payee_id: int,
    payload: PayeeChangeIn,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        payee = request_change(db, payee_id, payload.model_dump(exclude_unset=True), reason=payload.reason, operator=_operator(request))
    except PayeeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return payee_dict(payee)


@router.get("/payees/changes")
def payee_changes(status: str = "", payee_id: int | None = None, db: Session = Depends(get_db)):
    return {"items": [change_dict(c) for c in list_changes(db, status, payee_id)]}


@router.post("/payees/changes/{change_id}/approve")
def approve_payee_change(change_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        change = approve_change(db, change_id, operator=_operator(request))
    except PayeeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return change_dict(change)


@router.post("/payees/changes/{change_id}/reject")
def reject_payee_change(change_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        change = reject_change(db, change_id, operator=_operator(request))
    except PayeeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return change_dict(change)
