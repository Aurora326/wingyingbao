"""农行多级账簿匹配。

明源云单据的付款账号往往是农行主账户，经办人没有选到具体项目子账簿。
这里按单据项目名称匹配主账户下的子账簿，落成“主账户-账簿号”。
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BankAccount, PaymentDoc, SubAccount

_COMPANY_SUFFIXES = ("土地储备项目", "配套商品房基地", "项目", "地块", "工程", "费用")
_NOISE_RE = re.compile(r"[（）()【】\[\]、，,。.\s/·\-—_]")


def _normalize(text: str) -> str:
    value = str(text or "")
    value = _NOISE_RE.sub("", value)
    for suffix in _COMPANY_SUFFIXES:
        value = value.replace(suffix, "")
    return value


def _bigrams(value: str) -> set[str]:
    if len(value) < 2:
        return {value} if value else set()
    return {value[i : i + 2] for i in range(len(value) - 1)}


def _score(left: str, right: str) -> float:
    a = _normalize(left)
    b = _normalize(right)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    return 2 * len(ga & gb) / (len(ga) + len(gb))


def match_sub_account(
    db: Session, payer_account_no: str, project_name: str
) -> tuple[BankAccount, SubAccount] | None:
    """只有项目名称能明确对上子账簿时才返回，避免把单据改错。"""
    account_no = "".join(str(payer_account_no or "").split())
    if not account_no or "-" in account_no:
        return None
    parent = db.execute(
        select(BankAccount).where(BankAccount.account_no == account_no)
    ).scalars().first()
    if parent is None:
        return None
    subs = db.execute(
        select(SubAccount).where(
            SubAccount.parent_account_id == parent.id,
            SubAccount.status == "正常",
        )
    ).scalars().all()
    if not subs:
        return None
    best: SubAccount | None = None
    best_score = 0.0
    for sub in subs:
        score = max(
            _score(project_name, sub.ledger_name or ""),
            _score(project_name, sub.project_name or ""),
        )
        if score > best_score:
            best, best_score = sub, score
    if best is None or best_score < 0.95:
        return None
    return parent, best


def sub_account_no(parent: BankAccount, sub: SubAccount) -> str:
    return f"{parent.account_no}-{sub.sub_no}"


def backfill_docs(db: Session) -> dict:
    """把存量单据里选了主账户的，按项目名称补成主账户-账簿号。"""
    parents = db.execute(
        select(BankAccount.account_no).where(
            BankAccount.id.in_(
                select(SubAccount.parent_account_id).where(SubAccount.parent_account_id.isnot(None))
            )
        )
    ).scalars().all()
    if not parents:
        return {"matched": 0, "unmatched": 0}
    docs = db.execute(
        select(PaymentDoc).where(
            PaymentDoc.payer_sub_account_id.is_(None),
            PaymentDoc.payer_account_no.in_(parents),
        )
    ).scalars().all()
    matched = unmatched = 0
    for doc in docs:
        ledger = match_sub_account(db, doc.payer_account_no, doc.project_name)
        if ledger is None:
            unmatched += 1
            continue
        parent, sub = ledger
        doc.payer_sub_account_id = sub.id
        doc.payer_account_no = sub_account_no(parent, sub)
        matched += 1
    db.commit()
    return {"matched": matched, "unmatched": unmatched}
