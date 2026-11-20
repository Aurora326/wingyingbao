"""付款单据与银行流水的自动匹配。

规则：同一付款账户、借方支出、金额完全一致，收款方账号或户名吻合，
且在单据日期前后窗口内；只有唯一最佳候选才自动匹配。
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PaymentDoc, Statement
from app.services.audit import write_audit
from app.services.recon import normalize_name

APPLY_AFTER_TXN_DAYS = 30
APPLY_BEFORE_TXN_DAYS = 90
SCORE_THRESHOLD = 0.9
MATCH_STATUSES = ("ready", "drafted")


def _normalize_account(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", value or "")


def _score(statement: Statement, doc: PaymentDoc) -> float:
    score = 0.0
    if doc.amount == statement.amount:
        score += 0.5
    doc_account = _normalize_account(doc.payee_account_no)
    stmt_account = _normalize_account(statement.counterparty_account)
    if doc_account and stmt_account and doc_account == stmt_account:
        score += 0.3
    else:
        name_doc = normalize_name(doc.payee_name)
        name_stmt = normalize_name(statement.counterparty_name)
        if name_doc and name_stmt and (name_doc in name_stmt or name_stmt in name_doc):
            score += 0.2
    days = abs((statement.txn_date - doc.apply_date).days) if doc.apply_date else 999
    if days == 0:
        score += 0.2
    elif days <= 3:
        score += 0.15
    elif days <= 7:
        score += 0.1
    return round(min(score, 1.0), 2)


def _candidates(db: Session, statement: Statement) -> list[tuple[float, PaymentDoc]]:
    if statement.direction != "debit":
        return []
    if statement.txn_date is None:
        return []
    start = statement.txn_date - dt.timedelta(days=APPLY_BEFORE_TXN_DAYS)
    end = statement.txn_date + dt.timedelta(days=APPLY_AFTER_TXN_DAYS)
    docs = db.execute(
        select(PaymentDoc).where(
            PaymentDoc.payer_account_id == statement.account_id,
            PaymentDoc.amount == statement.amount,
            PaymentDoc.status.in_(MATCH_STATUSES),
            PaymentDoc.payment_status != "paid",
            PaymentDoc.apply_date.isnot(None),
            PaymentDoc.apply_date >= start,
            PaymentDoc.apply_date <= end,
        )
    ).scalars().all()
    return [(_score(statement, doc), doc) for doc in docs]


def _unique_best(scored: list[tuple[float, PaymentDoc]]) -> PaymentDoc | None:
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0], reverse=True)
    best_score, best_doc = scored[0]
    if best_score < SCORE_THRESHOLD:
        return None
    if len(scored) > 1 and scored[1][0] == best_score:
        return None  # 并列候选，留给人工确认
    return best_doc


def _apply(db: Session, statement: Statement, doc: PaymentDoc, match_type: str = "auto") -> None:
    statement.matched_payment_doc_id = doc.id
    statement.payment_match_type = match_type
    statement.payment_matched_at = dt.datetime.now()
    doc.payment_status = "paid"
    doc.paid_amount = statement.amount
    write_audit(
        db,
        "system",
        "payment_matched",
        "payment_doc",
        doc.id,
        {
            "statement_id": statement.id,
            "serial_no": statement.serial_no,
            "txn_date": statement.txn_date.isoformat() if statement.txn_date else "",
            "amount": str(statement.amount),
            "match_type": match_type,
        },
    )


def match_statement(db: Session, statement: Statement) -> PaymentDoc | None:
    """为单条流水匹配付款单据；已匹配或非支出流水直接跳过。"""
    if statement.matched_payment_doc_id or statement.direction != "debit":
        return None
    doc = _unique_best(_candidates(db, statement))
    if doc is None:
        return None
    _apply(db, statement, doc)
    return doc


def match_statements_for_account(db: Session, account_id: int) -> int:
    """流水入库后，对该账户未匹配的支出流水做一次匹配。"""
    statements = db.execute(
        select(Statement).where(
            Statement.account_id == account_id,
            Statement.direction == "debit",
            Statement.matched_payment_doc_id.is_(None),
        )
    ).scalars().all()
    return sum(1 for statement in statements if match_statement(db, statement))


def match_statements_for_document(db: Session, doc: PaymentDoc) -> int:
    """单据制单完成后，用已有流水反向匹配该单据。"""
    if doc.payer_account_id is None or doc.apply_date is None:
        return 0
    if doc.payment_status == "paid" or doc.status not in MATCH_STATUSES:
        return 0
    statements = db.execute(
        select(Statement).where(
            Statement.account_id == doc.payer_account_id,
            Statement.direction == "debit",
            Statement.matched_payment_doc_id.is_(None),
            Statement.amount == doc.amount,
            Statement.txn_date >= doc.apply_date - dt.timedelta(days=APPLY_BEFORE_TXN_DAYS),
            Statement.txn_date <= doc.apply_date + dt.timedelta(days=APPLY_AFTER_TXN_DAYS),
        )
    ).scalars().all()
    scored = sorted(
        ((_score(statement, doc), statement) for statement in statements),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not scored or scored[0][0] < SCORE_THRESHOLD:
        return 0
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return 0
    _apply(db, scored[0][1], doc)
    return 1


def rematch_unmatched(db: Session, account_id: int | None = None) -> int:
    """补跑历史匹配，可按账户过滤。"""
    query = select(Statement).where(
        Statement.direction == "debit",
        Statement.matched_payment_doc_id.is_(None),
    )
    if account_id:
        query = query.where(Statement.account_id == account_id)
    statements = db.execute(query).scalars().all()
    return sum(1 for statement in statements if match_statement(db, statement))
