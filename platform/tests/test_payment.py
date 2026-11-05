from __future__ import annotations

import datetime as dt
from decimal import Decimal

from openpyxl import Workbook
from sqlalchemy import select

from app.models import BankAccount, Enterprise, PayeeAccount, PaymentDoc, PaymentDocChange
from app.services.payment import (
    STATUS_DRAFTED,
    STATUS_PENDING_CONFIRM,
    STATUS_PENDING_REVIEW,
    STATUS_READY,
    confirm_accounts,
    export_draft,
    import_payment_excel,
    review_doc,
)


def _sample_excel(path):
    wb = Workbook()
    ws = wb.active
    ws.title = "付款申请"
    ws.append(
        [
            "序号", "业务ID", "申请编号", "申请主题", "申请人", "申请部门", "申请日期",
            "付款审批类型", "收款单位", "收款账号", "收款开户行", "付款单位", "付款账户",
            "付款开户行", "申请金额", "实付金额", "项目信息", "合同名称", "合同编号",
            "流程状态", "审批节点数", "审批流程", "采集时间",
        ]
    )
    ws.append(
        [
            1, "GUID-001", "A0298-TEST-0001", "测试工程款", "王红", "资产经营部",
            "2026-10-30", "合同付款审批", "测试收款单位", "TEST-ACCT-001",
            "测试银行第一分行", "测试付款单位", "TEST-ACCT-002",
            "测试银行第二分行", 219404.71, 219404.71, "测试项目",
            "前期物业服务合同", "HT-001", "已归档", 2, "开始 -> 部门负责人", "2026-11-05 12:00:00",
        ]
    )
    ws2 = wb.create_sheet("审批过程")
    ws2.append(["序号", "申请编号", "申请主题", "审批节点", "审批人", "审批结果", "审批意见", "审批时间"])
    ws2.append([1, "A0298-TEST-0001", "测试工程款", "开始", "王红", "发起", "请审批", "2026-10-30 10:00:00"])
    ws2.append([2, "A0298-TEST-0001", "测试工程款", "部门负责人", "王汇", "同意", "拟同意", "2026-10-30 11:00:00"])
    wb.save(path)


def test_import_confirm_review_and_draft(db, tmp_path):
    path = tmp_path / "付款申请.xlsx"
    _sample_excel(path)

    result = import_payment_excel(db, path)
    assert result["inserted"] == 1
    assert result["payees_created"] == 1

    doc = db.execute(select(PaymentDoc).where(PaymentDoc.apply_no == "A0298-TEST-0001")).scalars().one()
    assert doc.status == STATUS_PENDING_CONFIRM
    assert doc.amount == Decimal("219404.71")
    assert doc.payee_account_no == "TEST-ACCT-001"
    assert len(doc.approval_steps) == 2

    payee = db.execute(select(PayeeAccount).where(PayeeAccount.account_no == "TEST-ACCT-001")).scalars().one()
    enterprise = db.execute(
        select(Enterprise).where(Enterprise.name == "测试付款单位")
    ).scalars().one()
    payer = BankAccount(
        enterprise_id=enterprise.id,
        bank_code="ccb",
        account_no="TEST-ACCT-002",
        account_name="测试基本户",
    )
    db.add(payer)
    db.commit()

    confirm_accounts(db, doc.id, payer.id, payee.id, reason="测试确认", operator="tester")
    db.refresh(doc)
    assert doc.status == STATUS_PENDING_REVIEW
    assert doc.payer_account_id == payer.id
    assert db.execute(select(PaymentDocChange).where(PaymentDocChange.doc_id == doc.id)).scalars().all()

    review_doc(db, doc.id, "approve", opinion="同意制单", reviewer="cashier")
    db.refresh(doc)
    assert doc.status == STATUS_READY

    draft_path, count = export_draft(db, [doc.id], operator="cashier")
    assert count == 1
    assert draft_path.exists()
    db.refresh(doc)
    assert doc.status == STATUS_DRAFTED
    assert doc.draft_ref == str(draft_path)

    again = import_payment_excel(db, path)
    assert again["skipped"] == 1
    assert again["inserted"] == 0


def test_import_is_idempotent_with_force(db, tmp_path):
    path = tmp_path / "付款申请.xlsx"
    _sample_excel(path)
    import_payment_excel(db, path)
    result = import_payment_excel(db, path, force=True)
    assert result["updated"] == 1
    count = db.execute(select(PaymentDoc).where(PaymentDoc.apply_no == "A0298-TEST-0001")).scalars().all()
    assert len(count) == 1
