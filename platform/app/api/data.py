from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Artifact, BankAccount, Receipt, Statement

router = APIRouter(prefix="/api", tags=["data"])


@router.get("/statements")
def list_statements(
    account_id: int | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    q: str | None = None,
    direction: str | None = None,
    page: int = 1,
    size: int = 50,
    db: Session = Depends(get_db),
):
    query = select(Statement)
    if account_id:
        query = query.where(Statement.account_id == account_id)
    if date_from:
        query = query.where(Statement.txn_date >= date_from)
    if date_to:
        query = query.where(Statement.txn_date <= date_to)
    if direction in ("credit", "debit"):
        query = query.where(Statement.direction == direction)
    if q:
        pattern = f"%{q}%"
        query = query.where(
            or_(
                Statement.counterparty_name.like(pattern),
                Statement.summary.like(pattern),
                Statement.counterparty_account.like(pattern),
                Statement.serial_no.like(pattern),
            )
        )
    total = db.execute(query.with_only_columns(func.count(Statement.id)).order_by(None)).scalar_one()
    rows = db.execute(
        query.order_by(Statement.txn_date.desc(), Statement.txn_time.desc())
        .offset(max(page - 1, 0) * size)
        .limit(min(size, 200))
    ).scalars().all()
    accounts = {a.id: a for a in db.execute(select(BankAccount)).scalars().all()}
    receipts = {
        r.matched_statement_id: r.id
        for r in db.execute(select(Receipt).where(Receipt.matched_statement_id.is_not(None))).scalars().all()
    }
    return {
        "total": total,
        "page": page,
        "size": size,
        "items": [
            {
                "id": s.id,
                "bank_code": accounts[s.account_id].bank_code if s.account_id in accounts else "",
                "account": accounts[s.account_id].account_no if s.account_id in accounts else "",
                "txn_date": s.txn_date,
                "txn_time": s.txn_time,
                "direction": s.direction,
                "amount": str(s.amount),
                "balance": str(s.balance) if s.balance is not None else "",
                "counterparty_name": s.counterparty_name,
                "counterparty_account": s.counterparty_account,
                "summary": s.summary,
                "serial_no": s.serial_no,
                "artifact_id": s.artifact_id,
                "receipt_id": receipts.get(s.id),
            }
            for s in rows
        ],
    }


@router.get("/receipts")
def list_receipts(
    account_id: int | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    q: str | None = None,
    page: int = 1,
    size: int = 50,
    db: Session = Depends(get_db),
):
    query = select(Receipt)
    if account_id:
        query = query.where(Receipt.account_id == account_id)
    if date_from:
        query = query.where(Receipt.txn_date >= date_from)
    if date_to:
        query = query.where(Receipt.txn_date <= date_to)
    if q:
        pattern = f"%{q}%"
        query = query.where(or_(Receipt.counterparty_name.like(pattern), Receipt.receipt_no.like(pattern)))
    total = db.execute(query.with_only_columns(func.count(Receipt.id)).order_by(None)).scalar_one()
    rows = db.execute(
        query.order_by(Receipt.id.desc()).offset(max(page - 1, 0) * size).limit(min(size, 200))
    ).scalars().all()
    accounts = {a.id: a for a in db.execute(select(BankAccount)).scalars().all()}
    return {
        "total": total,
        "page": page,
        "size": size,
        "items": [
            {
                "id": r.id,
                "bank_code": accounts[r.account_id].bank_code if r.account_id in accounts else "",
                "account": accounts[r.account_id].account_no if r.account_id in accounts else "",
                "receipt_no": r.receipt_no,
                "txn_date": r.txn_date,
                "amount": str(r.amount) if r.amount is not None else "",
                "counterparty_name": r.counterparty_name,
                "artifact_id": r.artifact_id,
                "matched_statement_id": r.matched_statement_id,
            }
            for r in rows
        ],
    }


@router.get("/artifacts")
def list_artifacts(account_id: int | None = None, limit: int = 100, db: Session = Depends(get_db)):
    query = select(Artifact).order_by(Artifact.id.desc()).limit(min(limit, 500))
    if account_id:
        query = query.where(Artifact.account_id == account_id)
    rows = db.execute(query).scalars().all()
    return {
        "items": [
            {
                "id": a.id,
                "kind": a.kind,
                "original_name": a.original_name,
                "size": a.size,
                "parsed_status": a.parsed_status,
                "parse_error": a.parse_error,
                "created_at": a.created_at,
            }
            for a in rows
        ]
    }
