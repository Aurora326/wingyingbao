from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import select

from app.models import AuditLog, BankAccount, Enterprise, PaymentDoc, Statement
from app.services.payment_match import (
    match_statements_for_account,
    match_statements_for_document,
    match_statement,
)


def _payer(db, suffix: str) -> BankAccount:
    enterprise = Enterprise(name="付款匹配测试单位")
    db.add(enterprise)
    db.flush()
    account = BankAccount(
        enterprise_id=enterprise.id,
        bank_code="ccb",
        account_no=f"MATCH-{suffix}",
        account_name="测试付款户",
    )
    db.add(account)
    db.flush()
    return account


def _doc(db, account: BankAccount, suffix: str, amount: str, apply_date: dt.date) -> PaymentDoc:
    doc = PaymentDoc(
        apply_no=f"MATCH-{suffix}",
        payer_account_id=account.id,
        payer_account_no=account.account_no,
        payer_name=account.account_name,
        payee_name="上海测试收款有限公司",
        payee_account_no="6222000000001234",
        amount=Decimal(amount),
        apply_date=apply_date,
        status="ready",
    )
    db.add(doc)
    db.flush()
    return doc


def _statement(db, account: BankAccount, suffix: str, amount: str, txn_date: dt.date) -> Statement:
    statement = Statement(
        account_id=account.id,
        txn_date=txn_date,
        direction="debit",
        amount=Decimal(amount),
        counterparty_name="上海测试收款有限公司",
        counterparty_account="6222000000001234",
        summary="付款",
        serial_no=f"SN-{suffix}",
        dedupe_hash=f"hash-{suffix}",
    )
    db.add(statement)
    db.flush()
    return statement


def test_match_unique_document(db):
    account = _payer(db, "UNIQUE")
    day = dt.date(2026, 11, 10)
    doc = _doc(db, account, "UNIQUE", "10000.00", day)
    statement = _statement(db, account, "UNIQUE", "10000.00", day)

    matched = match_statement(db, statement)
    db.flush()

    assert matched is not None and matched.id == doc.id
    assert statement.matched_payment_doc_id == doc.id
    assert doc.payment_status == "paid"
    assert doc.paid_amount == Decimal("10000.00")
    log = db.execute(
        select(AuditLog).where(
            AuditLog.action == "payment_matched",
            AuditLog.target_type == "payment_doc",
            AuditLog.target_id == str(doc.id),
        )
    ).scalars().one()
    assert log.detail["statement_id"] == statement.id


def test_match_skips_amount_mismatch(db):
    account = _payer(db, "AMOUNT")
    day = dt.date(2026, 11, 10)
    _doc(db, account, "AMOUNT", "10000.00", day)
    statement = _statement(db, account, "AMOUNT", "9999.00", day)

    assert match_statement(db, statement) is None
    assert statement.matched_payment_doc_id is None


def test_match_skips_ambiguous_candidates(db):
    account = _payer(db, "AMBIG")
    day = dt.date(2026, 11, 10)
    _doc(db, account, "AMBIG-1", "5000.00", day)
    _doc(db, account, "AMBIG-2", "5000.00", day)
    statement = _statement(db, account, "AMBIG", "5000.00", day)

    assert match_statement(db, statement) is None
    assert statement.matched_payment_doc_id is None


def test_match_respects_date_window(db):
    account = _payer(db, "WINDOW")
    _doc(db, account, "WINDOW", "3000.00", dt.date(2025, 1, 1))
    statement = _statement(db, account, "WINDOW", "3000.00", dt.date(2026, 11, 10))

    assert match_statement(db, statement) is None


def test_document_matches_existing_statement(db):
    account = _payer(db, "REVERSE")
    day = dt.date(2026, 11, 10)
    statement = _statement(db, account, "REVERSE", "8000.00", day)
    doc = _doc(db, account, "REVERSE", "8000.00", day)

    assert match_statements_for_document(db, doc) == 1
    db.flush()
    assert statement.matched_payment_doc_id == doc.id
    assert doc.payment_status == "paid"


def test_account_rematch_matches_unmatched_statements(db):
    account = _payer(db, "BATCH")
    day = dt.date(2026, 11, 10)
    _doc(db, account, "BATCH", "1200.00", day)
    _statement(db, account, "BATCH", "1200.00", day)

    assert match_statements_for_account(db, account.id) == 1
