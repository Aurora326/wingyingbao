"""从付款单据沉淀收/付款基础资料。

收款方沉淀到收款账户库（PayeeAccount），付款方对齐到账户主数据（BankAccount）。
只做补全和关联，不覆盖出纳已经维护过的字段。
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BankAccount, Enterprise, PayeeAccount, PaymentDoc

# 按顺序匹配，先长后短，避免「中国银行」误伤「中国建设银行」这类名称
_BANK_KEYWORDS = (
    ("工商银行", "icbc"),
    ("建设银行", "ccb"),
    ("农业银行", "abc"),
    ("中国银行", "boc"),
    ("中信银行", "citic"),
    ("兴业银行", "cib"),
    ("上海银行", "bosh"),
    ("宁波银行", "nb"),
    ("南京银行", "njcb"),
    ("广发银行", "cgb"),
    ("农商", "shrcb"),
    ("招商银行", "cmb"),
    ("工行", "icbc"),
    ("建行", "ccb"),
    ("农行", "abc"),
    ("中行", "boc"),
    ("中信", "citic"),
    ("兴业", "cib"),
    ("招行", "cmb"),
)

_ACCOUNT_RE = re.compile(r"^[0-9]{8,32}$")


def _text(value) -> str:
    return "".join(str(value or "").split())


def infer_bank_code(bank_name: str) -> str:
    text = _text(bank_name)
    for keyword, code in _BANK_KEYWORDS:
        if keyword in text:
            return code
    return ""


def _valid_account(account_no: str) -> bool:
    return bool(_ACCOUNT_RE.match(account_no))


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
    created = linked = 0
    unmatched = 0
    enterprises = {e.name: e for e in db.execute(select(Enterprise)).scalars().all()}
    docs = db.execute(select(PaymentDoc).where(PaymentDoc.payer_name != "")).scalars().all()
    for doc in docs:
        name = _text(doc.payer_name)
        account_no = _text(doc.payer_account_no)
        if not name or not _valid_account(account_no):
            if doc.payer_account_id is None:
                unmatched += 1
            continue
        account = db.execute(
            select(BankAccount).where(BankAccount.account_no == account_no)
        ).scalars().first()
        if account is None:
            enterprise = enterprises.get(name)
            bank_code = infer_bank_code(doc.payer_bank)
            if enterprise is None or not bank_code:
                unmatched += 1
                continue
            account = BankAccount(
                enterprise_id=enterprise.id,
                bank_code=bank_code,
                account_no=account_no,
                account_name=name[:128],
                currency=doc.currency or "CNY",
                collect_enabled=False,
                payment_enabled=True,
                has_ukey=False,
                usage_tag="单据同步",
            )
            db.add(account)
            db.flush()
            created += 1
        if doc.payer_account_id != account.id:
            doc.payer_account_id = account.id
            linked += 1
    db.commit()
    return {"created": created, "linked": linked, "unmatched": unmatched}


def sync_payment_master(db: Session) -> dict:
    return {"payees": sync_payees(db), "payers": sync_payers(db)}
