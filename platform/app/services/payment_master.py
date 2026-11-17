"""从付款单据沉淀收付款基础资料。

收款方沉淀到收款账户库（PayeeAccount）；付款方只关联已有的银行账户，不新建。
只做补全和关联，不覆盖出纳已经维护过的字段。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BankAccount, PayeeAccount, PaymentDoc


def _text(value) -> str:
    return "".join(str(value or "").split())


def sync_payees(db: Session) -> dict:
    created = linked = enriched = 0
    docs = db.execute(select(PaymentDoc).where(PaymentDoc.payee_name != "")).scalars().all()
    for doc in docs:
        name = _text(doc.payee_name)
        account_no = _text(doc.payee_account_no)
        if not name:
            continue
        payee = None
        if account_no:
            payee = db.execute(
                select(PayeeAccount).where(PayeeAccount.account_no == account_no)
            ).scalars().first()
        else:
            payee = db.execute(
                select(PayeeAccount).where(PayeeAccount.payee_name == name)
            ).scalars().first()
        if payee is None and not account_no:
            continue
        if payee is None:
            payee = PayeeAccount(
                payee_name=name[:256],
                account_no=account_no,
                bank_name=_text(doc.payee_bank)[:128],
                branch_name=_text(doc.payee_branch)[:128],
                currency=doc.currency or "CNY",
                status="active",
                source=doc.source_system or "单据同步",
            )
            db.add(payee)
            db.flush()
            created += 1
        else:
            changed = False
            if doc.payee_bank and not payee.bank_name:
                payee.bank_name = _text(doc.payee_bank)[:128]
                changed = True
            if doc.payee_branch and not payee.branch_name:
                payee.branch_name = _text(doc.payee_branch)[:128]
                changed = True
            if changed:
                enriched += 1
        if doc.payee_account_id != payee.id:
            doc.payee_account_id = payee.id
            linked += 1
    db.commit()
    return {"created": created, "linked": linked, "enriched": enriched}


def sync_payers(db: Session) -> dict:
    """付款账户由「账户管理」维护，这里只把单据关联到已有账户，不新建。"""
    linked = 0
    unmatched = 0
    docs = db.execute(select(PaymentDoc).where(PaymentDoc.payer_name != "")).scalars().all()
    for doc in docs:
        account_no = _text(doc.payer_account_no)
        if not account_no:
            if doc.payer_account_id is None:
                unmatched += 1
            continue
        account = db.execute(
            select(BankAccount).where(BankAccount.account_no == account_no)
        ).scalars().first()
        if account is None:
            unmatched += 1
            continue
        if doc.payer_account_id != account.id:
            doc.payer_account_id = account.id
            linked += 1
    db.commit()
    return {"created": 0, "linked": linked, "unmatched": unmatched}


def sync_payment_master(db: Session) -> dict:
    return {"payees": sync_payees(db), "payers": sync_payers(db)}
