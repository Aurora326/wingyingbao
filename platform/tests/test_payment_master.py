from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.models import BankAccount, Enterprise, PayeeAccount, PaymentDoc
from app.services.payment_master import sync_payment_master


def _doc(**overrides) -> PaymentDoc:
    values = dict(
        apply_no="SYNC-TEST-0001",
        subject="同步测试",
        payee_name="测试收款方甲",
        payee_account_no="6222000000001234",
        payee_bank="中国工商银行上海分行",
        payee_branch="徐汇支行",
        payer_name="测试付款单位甲",
        payer_account_no="3105016100009999",
        payer_bank="中国建设银行上海浦东分行",
        amount=Decimal("100.00"),
        currency="CNY",
        source_system="明源云",
    )
    values.update(overrides)
    return PaymentDoc(**values)


def test_sync_builds_payee_and_payer_master(db):
    db.add(Enterprise(name="测试付款单位甲"))
    db.add(_doc())
    db.commit()

    result = sync_payment_master(db)
    assert result["payees"]["created"] >= 1
    assert result["payers"]["created"] >= 1

    doc = db.execute(select(PaymentDoc).where(PaymentDoc.apply_no == "SYNC-TEST-0001")).scalars().one()
    payee = db.execute(
        select(PayeeAccount).where(PayeeAccount.account_no == "6222000000001234")
    ).scalars().one()
    payer = db.execute(
        select(BankAccount).where(BankAccount.account_no == "3105016100009999")
    ).scalars().one()
    assert doc.payee_account_id == payee.id
    assert doc.payer_account_id == payer.id
    assert payer.bank_code == "ccb"
    assert payer.collect_enabled is False
    assert payee.branch_name == "徐汇支行"

    again = sync_payment_master(db)
    assert again["payees"]["created"] == 0
    assert again["payers"]["created"] == 0


def test_sync_leaves_invalid_accounts_unmatched(db):
    db.add(Enterprise(name="测试付款单位乙"))
    db.add(
        _doc(
            apply_no="SYNC-TEST-0002",
            payer_name="测试付款单位乙",
            payer_account_no="不是账号",
            payee_name="测试收款方乙",
            payee_account_no="6222000000005678",
        )
    )
    db.commit()

    result = sync_payment_master(db)
    assert result["payers"]["unmatched"] >= 1
    assert db.execute(
        select(BankAccount).where(BankAccount.account_no == "不是账号")
    ).scalars().first() is None
