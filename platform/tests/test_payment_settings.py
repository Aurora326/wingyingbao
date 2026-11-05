from __future__ import annotations

from decimal import Decimal

from openpyxl import Workbook
from sqlalchemy import func, select

from app.models import BankAccount, BookSet, PayeeAccount, PayeeAccountChange, SubAccount
from app.services.account_master import account_master_summary, import_account_master
from app.services.payee_admin import create_payee, request_change


def _accounts_excel(path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["单位名称", "银行账户号码", "开户行", "用途"])
    ws.append(["测试公司A", "1001（无盾）", "建设银行浦东分行", "基本户"])
    ws.append(["测试公司A", "2001（有子账户）", "农业银行浦东分行", "一般户"])
    ws.append(["测试公司B", "3001（挂一般户下面）", "测试银行浦东分行", "贷款户"])
    ws2 = wb.create_sheet("农行子账户")
    ws2.append(["多级账簿余额查询"])
    ws2.append(["账簿名", "账簿号", "币种", "操作结果"])
    ws2.append(["测试公司A项目一", "0000000001", "人民币", "查询成功"])
    ws2.append(["测试公司A项目二", "0000000002", "人民币", "查询成功"])
    wb.save(path)


def test_import_account_master(db, tmp_path):
    path = tmp_path / "accounts.xlsx"
    _accounts_excel(path)
    result = import_account_master(db, path)
    assert result["accounts_created"] == 3
    assert result["sub_accounts_created"] == 2

    summary = account_master_summary(db)
    assert summary["total"] >= 3
    assert summary["payable"] >= 2
    assert summary["loan"] >= 1
    assert summary["sub_accounts"] == 2
    assert summary["by_type"].get("基本户", 0) >= 1

    basic = db.execute(select(BankAccount).where(BankAccount.account_no == "1001")).scalars().one()
    assert basic.account_type == "基本户"
    assert basic.payment_enabled is True
    assert basic.has_ukey is False

    parent = db.execute(select(BankAccount).where(BankAccount.account_no == "2001")).scalars().one()
    assert parent.has_sub_accounts is True
    subs = db.execute(select(SubAccount).where(SubAccount.parent_account_id == parent.id)).scalars().all()
    assert {s.sub_no for s in subs} == {"0000000001", "0000000002"}

    book = db.execute(select(BookSet).where(BookSet.enterprise_id == basic.enterprise_id)).scalars().one()
    assert book.name == "测试公司A"


def test_payee_change_applies_immediately(db):
    payee = create_payee(
        db,
        {"payee_name": "变更测试单位", "account_no": "90001", "bank_name": "原开户行"},
        operator="maker",
    )
    payee = request_change(db, payee.id, {"bank_name": "新开户行"}, reason="账户变更", operator="cashier")
    assert payee.bank_name == "新开户行"

    payee = request_change(db, payee.id, {"status": "disabled"}, reason="", operator="cashier")
    assert payee.status == "disabled"
    assert db.execute(select(func.count(PayeeAccountChange.id))).scalar_one() == 0
