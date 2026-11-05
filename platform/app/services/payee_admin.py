from __future__ import annotations

import datetime as dt

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import PayeeAccount, PayeeAccountChange

PAYEE_FIELDS = ("payee_name", "account_no", "bank_name", "branch_name", "cnaps_code", "currency", "status")


class PayeeError(Exception):
    pass


def payee_dict(payee: PayeeAccount) -> dict:
    return {
        "id": payee.id,
        "payee_name": payee.payee_name,
        "account_no": payee.account_no,
        "bank_name": payee.bank_name,
        "branch_name": payee.branch_name,
        "cnaps_code": payee.cnaps_code,
        "currency": payee.currency,
        "status": payee.status,
        "source": payee.source,
    }


def change_dict(change: PayeeAccountChange) -> dict:
    return {
        "id": change.id,
        "payee_account_id": change.payee_account_id,
        "action": change.action,
        "old_values": change.old_values or {},
        "new_values": change.new_values or {},
        "reason": change.reason,
        "status": change.status,
        "requested_by": change.requested_by,
        "approved_by": change.approved_by,
        "approved_at": change.approved_at.isoformat() if change.approved_at else "",
    }


def list_payees(db: Session, q: str = "", status: str = "") -> list[PayeeAccount]:
    query = select(PayeeAccount)
    if status:
        query = query.where(PayeeAccount.status == status)
    if q:
        pattern = f"%{q}%"
        query = query.where(
            or_(
                PayeeAccount.payee_name.like(pattern),
                PayeeAccount.account_no.like(pattern),
                PayeeAccount.bank_name.like(pattern),
            )
        )
    return db.execute(query.order_by(PayeeAccount.id.desc()).limit(500)).scalars().all()


def create_payee(db: Session, values: dict, operator: str = "") -> PayeeAccount:
    name = str(values.get("payee_name") or "").strip()
    account_no = str(values.get("account_no") or "").strip()
    if not name or not account_no:
        raise PayeeError("收款单位和收款账号不能为空")
    exists = db.execute(
        select(PayeeAccount).where(
            PayeeAccount.payee_name == name,
            PayeeAccount.account_no == account_no,
        )
    ).scalars().first()
    if exists is not None:
        raise PayeeError("该收款账户已存在")
    payee = PayeeAccount(
        payee_name=name,
        account_no=account_no,
        bank_name=str(values.get("bank_name") or "").strip(),
        branch_name=str(values.get("branch_name") or "").strip(),
        cnaps_code=str(values.get("cnaps_code") or "").strip(),
        currency=str(values.get("currency") or "CNY"),
        status="active",
        source="手工",
    )
    db.add(payee)
    db.commit()
    db.refresh(payee)
    return payee


def request_change(db: Session, payee_id: int, values: dict, reason: str = "", operator: str = "") -> PayeeAccount:
    """出纳直接变更收款账户，保存即生效，不产生审批或变更记录。"""
    payee = db.get(PayeeAccount, payee_id)
    if payee is None:
        raise PayeeError("收款账户不存在")
    new_values = {key: values[key] for key in PAYEE_FIELDS if key in values and values[key] is not None}
    if not new_values:
        raise PayeeError("没有需要变更的字段")
    for key, value in new_values.items():
        setattr(payee, key, value)
    db.commit()
    db.refresh(payee)
    return payee


def approve_change(db: Session, change_id: int, operator: str = "") -> PayeeAccountChange:
    change = db.get(PayeeAccountChange, change_id)
    if change is None:
        raise PayeeError("变更记录不存在")
    if change.status != "pending":
        raise PayeeError("该变更已处理")
    payee = db.get(PayeeAccount, change.payee_account_id)
    if payee is None:
        raise PayeeError("收款账户不存在")
    for key, value in (change.new_values or {}).items():
        if key in PAYEE_FIELDS:
            setattr(payee, key, value)
    change.status = "approved"
    change.approved_by = operator
    change.approved_at = dt.datetime.now()
    db.commit()
    db.refresh(change)
    return change


def reject_change(db: Session, change_id: int, operator: str = "") -> PayeeAccountChange:
    change = db.get(PayeeAccountChange, change_id)
    if change is None:
        raise PayeeError("变更记录不存在")
    if change.status != "pending":
        raise PayeeError("该变更已处理")
    change.status = "rejected"
    change.approved_by = operator
    change.approved_at = dt.datetime.now()
    db.commit()
    db.refresh(change)
    return change


def list_changes(db: Session, status: str = "", payee_id: int | None = None) -> list[PayeeAccountChange]:
    query = select(PayeeAccountChange)
    if status:
        query = query.where(PayeeAccountChange.status == status)
    if payee_id:
        query = query.where(PayeeAccountChange.payee_account_id == payee_id)
    return db.execute(query.order_by(PayeeAccountChange.id.desc()).limit(200)).scalars().all()
