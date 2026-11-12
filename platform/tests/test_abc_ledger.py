from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.models import BankAccount, Enterprise, PaymentDoc, SubAccount
from app.services.abc_ledger import backfill_docs, match_sub_account


def _setup(db):
    existing = db.execute(
        select(BankAccount).where(BankAccount.account_no == "0334999900000001")
    ).scalars().first()
    if existing is not None:
        return existing
    enterprise = Enterprise(name="测试农行单位")
    db.add(enterprise)
    db.flush()
    parent = BankAccount(
        enterprise_id=enterprise.id,
        bank_code="abc",
        account_no="0334999900000001",
        account_name="测试农行单位",
    )
    db.add(parent)
    db.flush()
    db.add_all(
        [
            SubAccount(
                parent_account_id=parent.id,
                sub_no="0000000001",
                ledger_name="测试农行单位三林恒大项目",
                project_name="三林恒大项目",
            ),
            SubAccount(
                parent_account_id=parent.id,
                sub_no="0000000002",
                ledger_name="测试农行单位塔水桥项目",
                project_name="塔水桥项目",
            ),
        ]
    )
    db.commit()
    return parent


def _doc(apply_no: str, project: str, payer_account_no: str) -> PaymentDoc:
    return PaymentDoc(
        apply_no=apply_no,
        project_name=project,
        payer_name="测试农行单位",
        payer_account_no=payer_account_no,
        amount=Decimal("1"),
    )


def test_backfill_matches_project_to_sub_account(db):
    parent = _setup(db)
    db.add_all(
        [
            _doc("ABC-SUB-0001", "三林恒大项目土地储备", parent.account_no),
            _doc("ABC-SUB-0002", "塔水桥项目", parent.account_no),
            _doc("ABC-SUB-0003", "完全无关的项目", parent.account_no),
        ]
    )
    db.commit()

    result = backfill_docs(db)
    assert result == {"matched": 2, "unmatched": 1}

    docs = {
        doc.apply_no: doc
        for doc in db.execute(
            select(PaymentDoc).where(PaymentDoc.apply_no.like("ABC-SUB-%"))
        ).scalars()
    }
    assert docs["ABC-SUB-0001"].payer_account_no == "0334999900000001-0000000001"
    assert docs["ABC-SUB-0002"].payer_account_no == "0334999900000001-0000000002"
    assert docs["ABC-SUB-0003"].payer_account_no == "0334999900000001"
    assert docs["ABC-SUB-0001"].payer_sub_account_id is not None


def test_match_skips_parent_without_project_match_and_sub_number(db):
    parent = _setup(db)
    assert match_sub_account(db, parent.account_no, "完全没有对应关系的支出") is None
    assert match_sub_account(db, f"{parent.account_no}-0000000001", "三林恒大项目") is None


def test_confirm_keeps_sub_account_number(db):
    from app.models import PayeeAccount
    from app.services.payment import confirm_accounts

    parent = _setup(db)
    sub = db.execute(
        select(SubAccount).where(
            SubAccount.parent_account_id == parent.id, SubAccount.sub_no == "0000000001"
        )
    ).scalars().one()
    payee = PayeeAccount(payee_name="确认测试收款方", account_no="6222000000009999", status="active")
    db.add(payee)
    db.flush()
    doc = PaymentDoc(
        apply_no="ABC-SUB-CONFIRM",
        apply_date=None,
        payer_name="测试农行单位",
        payer_account_no=f"{parent.account_no}-{sub.sub_no}",
        payer_account_id=parent.id,
        payer_sub_account_id=sub.id,
        payee_name=payee.payee_name,
        payee_account_no=payee.account_no,
        payee_account_id=payee.id,
        amount=Decimal("1"),
        status="pending_confirm",
    )
    db.add(doc)
    db.commit()

    confirm_accounts(db, doc.id, parent.id, payee.id, operator="tester")
    db.refresh(doc)
    assert doc.payer_account_no == f"{parent.account_no}-{sub.sub_no}"
