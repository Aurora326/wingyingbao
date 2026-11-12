from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook, load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    BankAccount,
    BookSet,
    Enterprise,
    PayeeAccount,
    PaymentDoc,
    PaymentDocChange,
    PaymentReview,
    SubAccount,
)
from app.services.parsers.statement import _to_date, _to_decimal, _to_text
from app.services.abc_ledger import match_sub_account, sub_account_no
from app.services.app_settings import ABC_LEDGER_AUTO_MATCH, get_bool

MAIN_SHEET = "付款申请"
STEP_SHEET = "审批过程"

STATUS_PENDING_CONFIRM = "pending_confirm"
STATUS_PENDING_REVIEW = "pending_review"
STATUS_READY = "ready"
STATUS_DRAFTED = "drafted"
STATUS_REJECTED = "rejected"


class PaymentError(Exception):
    pass


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _cell(row: dict[str, object], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return _to_text(value)
    return ""


def _amount(row: dict[str, object]) -> Decimal | None:
    for name in ("申请金额", "实付金额", "本次申请金额", "本次实付金额"):
        value = _to_decimal(row.get(name))
        if value is not None:
            return value
    return None


def _clean_project(value: str) -> str:
    """明源云项目信息常拼成「项目-某项目」或「项目-费用—某项目」，去掉尾部重复。"""
    parts = [_clean_project_part(part) for part in value.split(",")]
    return ",".join(parts)


def _clean_project_part(value: str) -> str:
    for index, char in enumerate(value):
        if char in "-—" and index > 0:
            tail = value[index + 1 :]
            if tail and value.startswith(tail):
                return value[:index]
    return value


def _read_sheet(path: Path, sheet_name: str) -> list[dict[str, object]]:
    wb = load_workbook(path, data_only=True)
    ws = wb[sheet_name] if sheet_name in wb.sheetnames else wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [_to_text(cell.value) for cell in ws[1]]
    result = []
    for raw in rows[1:]:
        if not raw or all(cell in (None, "") for cell in raw):
            continue
        result.append({headers[i]: raw[i] for i in range(min(len(headers), len(raw)))})
    return result


def _enterprise_for(db: Session, name: str) -> Enterprise:
    enterprise = db.execute(select(Enterprise).where(Enterprise.name == name)).scalars().first()
    if enterprise is None:
        enterprise = Enterprise(name=name or "未命名付款单位")
        db.add(enterprise)
        db.flush()
    return enterprise


def _book_set_for(db: Session, enterprise: Enterprise) -> BookSet:
    book_set = db.execute(select(BookSet).where(BookSet.enterprise_id == enterprise.id)).scalars().first()
    if book_set is None:
        book_set = BookSet(
            enterprise_id=enterprise.id,
            code=f"BS{enterprise.id:04d}",
            name=enterprise.name,
        )
        db.add(book_set)
        db.flush()
    elif book_set.name != enterprise.name:
        book_set.name = enterprise.name
    return book_set


def _payee_for(db: Session, row: dict[str, object]) -> tuple[PayeeAccount | None, bool]:
    name = _cell(row, "收款单位")
    account_no = _cell(row, "收款账号", "收款单位账户", "收款单位银行账户")
    bank = _cell(row, "收款开户行", "收款单位开户行", "开户行")
    if not name and not account_no:
        return None, False

    query = select(PayeeAccount)
    if account_no:
        # 账号在同一租户内唯一，按账号查找，避免收款单位名称微调时重复插入
        query = query.where(PayeeAccount.account_no == account_no)
    else:
        query = query.where(PayeeAccount.payee_name == name)
    payee = db.execute(query).scalars().first()
    if payee is None:
        payee = PayeeAccount(
            payee_name=name,
            account_no=account_no,
            bank_name=bank,
            source="明源云",
        )
        db.add(payee)
        db.flush()
        return payee, True
    if bank and not payee.bank_name:
        payee.bank_name = bank
    return payee, False


def _payer_for(db: Session, row: dict[str, object]) -> BankAccount | None:
    account_no = _cell(row, "付款账户", "付款单位账户")
    if not account_no:
        return None
    accounts = db.execute(select(BankAccount)).scalars().all()
    target = _digits(account_no)
    for account in accounts:
        if account.account_no == account_no or (_digits(account.account_no) and _digits(account.account_no) == target):
            return account
    return None


def import_payment_excel(db: Session, path: Path, force: bool = False) -> dict:
    """导入明源云双表 Excel：付款申请 + 审批过程。按申请编号幂等。"""
    main_rows = _read_sheet(path, MAIN_SHEET)
    step_rows = _read_sheet(path, STEP_SHEET) if STEP_SHEET in load_workbook(path, read_only=True).sheetnames else []
    steps_by_code: dict[str, list[dict]] = {}
    for step in step_rows:
        code = _cell(step, "申请编号")
        if not code:
            continue
        steps_by_code.setdefault(code, []).append(
            {
                "审批节点": _cell(step, "审批节点"),
                "审批人": _cell(step, "审批人"),
                "审批结果": _cell(step, "审批结果"),
                "审批意见": _cell(step, "审批意见"),
                "审批时间": _cell(step, "审批时间"),
            }
        )

    inserted = updated = skipped = payees_created = 0
    errors: list[str] = []
    for row in main_rows:
        apply_no = _cell(row, "申请编号")
        if not apply_no:
            skipped += 1
            continue
        if _cell(row, "审核状态") == "未审核":
            skipped += 1
            continue
        flow_status = _cell(row, "流程状态")
        amount = _amount(row)
        if amount is None:
            errors.append(f"{apply_no}: 缺少金额")
            skipped += 1
            continue

        payer_name = _cell(row, "付款单位")
        enterprise = _enterprise_for(db, payer_name)
        book_set = _book_set_for(db, enterprise)
        payer = _payer_for(db, row)
        payee, created = _payee_for(db, row)
        payees_created += 1 if created else 0

        project_name = _clean_project(_cell(row, "项目信息"))
        payer_account_no = _cell(row, "付款账户", "付款单位账户")
        ledger = None
        if get_bool(db, ABC_LEDGER_AUTO_MATCH, True):
            ledger = match_sub_account(db, payer_account_no, project_name)
        payer_sub_account_id = None
        if ledger is not None:
            parent, sub = ledger
            payer_sub_account_id = sub.id
            payer_account_no = sub_account_no(parent, sub)

        existing = db.execute(select(PaymentDoc).where(PaymentDoc.apply_no == apply_no)).scalars().first()
        steps = steps_by_code.get(apply_no, [])
        doc_type_label = _cell(row, "单据类型")
        if "无合同" in doc_type_label:
            doc_type = "non_contract"
        elif "报销" in doc_type_label or "划转" in doc_type_label:
            doc_type = "expense"
        else:
            doc_type = "contract"
        values = dict(
            book_set_id=book_set.id,
            enterprise_id=enterprise.id,
            source_system=_cell(row, "来源系统") or "明源云",
            doc_type=doc_type,
            subject=_cell(row, "申请主题", "主题"),
            applicant=_cell(row, "申请人"),
            department=_cell(row, "申请部门"),
            apply_date=_to_date(_cell(row, "申请日期")),
            approval_type=_cell(row, "付款审批类型"),
            contract_name=_cell(row, "合同名称"),
            contract_no=_cell(row, "合同编号"),
            project_name=project_name,
            payee_name=_cell(row, "收款单位") or (payee.payee_name if payee else ""),
            payee_account_no=_cell(row, "收款账号", "收款单位账户") or (payee.account_no if payee else ""),
            payee_bank=_cell(row, "收款开户行", "收款单位开户行") or (payee.bank_name if payee else ""),
            payer_name=payer_name,
            payer_account_no=payer_account_no,
            payer_sub_account_id=payer_sub_account_id,
            payer_bank=_cell(row, "付款开户行", "付款单位开户行"),
            payer_account_id=payer.id if payer else None,
            payee_account_id=payee.id if payee else None,
            amount=amount,
            paid_amount=_to_decimal(row.get("实付金额")),
            currency=_cell(row, "币种") or "CNY",
            approval_status=flow_status,
            payment_status=_cell(row, "支付状态"),
            approval_node=_cell(row, "当前节点"),
            approval_handler=_cell(row, "当前处理人"),
            approval_steps=steps,
            raw_json={"main": {k: (v.isoformat() if hasattr(v, "isoformat") else float(v) if isinstance(v, Decimal) else v) for k, v in row.items()}, "steps": steps},
        )

        if existing is None:
            db.add(PaymentDoc(apply_no=apply_no, status=STATUS_PENDING_CONFIRM, **values))
            inserted += 1
        elif force:
            for key, value in values.items():
                if key == "approval_steps" and not value and existing.approval_steps:
                    continue
                setattr(existing, key, value)
            if existing.status not in (STATUS_READY, STATUS_DRAFTED, "drafting"):
                existing.status = STATUS_PENDING_CONFIRM
            updated += 1
        else:
            skipped += 1

    db.commit()
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "payees_created": payees_created,
        "errors": errors[:20],
    }


def confirm_accounts(
    db: Session,
    doc_id: int,
    payer_account_id: int | None,
    payee_account_id: int | None,
    reason: str = "",
    operator: str = "",
) -> PaymentDoc:
    doc = db.get(PaymentDoc, doc_id)
    if doc is None:
        raise PaymentError("付款单据不存在")
    if doc.status not in (STATUS_PENDING_CONFIRM, STATUS_REJECTED):
        raise PaymentError("当前状态不允许确认账户")

    payer = db.get(BankAccount, payer_account_id) if payer_account_id else None
    payee = db.get(PayeeAccount, payee_account_id) if payee_account_id else None
    if payer is None:
        raise PaymentError("请选择付款账户")
    if payee is None:
        raise PaymentError("请选择收款账户")

    changes = [
        ("payer_account_id", doc.payer_account_id, payer.id),
        ("payee_account_id", doc.payee_account_id, payee.id),
        ("payee_account_no", doc.payee_account_no, payee.account_no),
        ("payee_bank", doc.payee_bank, payee.bank_name),
    ]
    for field, old, new in changes:
        if str(old or "") != str(new or ""):
            db.add(
                PaymentDocChange(
                    doc_id=doc.id,
                    field=field,
                    old_value=str(old or ""),
                    new_value=str(new or ""),
                    reason=reason,
                    operator=operator,
                )
            )

    doc.payer_account_id = payer.id
    payer_account_no = payer.account_no
    if doc.payer_sub_account_id:
        sub = db.get(SubAccount, doc.payer_sub_account_id)
        if sub is not None and sub.parent_account_id == payer.id:
            payer_account_no = sub_account_no(payer, sub)
        else:
            doc.payer_sub_account_id = None
    doc.payer_account_no = payer_account_no
    doc.payee_account_id = payee.id
    doc.payee_account_no = payee.account_no
    doc.payee_bank = payee.bank_name
    doc.status = STATUS_PENDING_REVIEW
    doc.confirmed_by = operator
    doc.confirmed_at = dt.datetime.now()
    doc.reject_reason = ""
    db.commit()
    return doc


def review_doc(db: Session, doc_id: int, action: str, opinion: str = "", reviewer: str = "") -> PaymentDoc:
    doc = db.get(PaymentDoc, doc_id)
    if doc is None:
        raise PaymentError("付款单据不存在")
    if doc.status != STATUS_PENDING_REVIEW:
        raise PaymentError("当前状态不允许审核")
    if action not in ("approve", "reject"):
        raise PaymentError("审核动作不合法")

    db.add(PaymentReview(doc_id=doc.id, reviewer=reviewer, action=action, opinion=opinion))
    doc.reviewed_by = reviewer
    doc.reviewed_at = dt.datetime.now()
    if action == "approve":
        doc.status = STATUS_READY
        doc.reject_reason = ""
    else:
        doc.status = STATUS_REJECTED
        doc.reject_reason = opinion
    db.commit()
    return doc


DRAFT_HEADERS = [
    "序号",
    "申请编号",
    "付款单位",
    "付款账户",
    "收款单位",
    "收款账号",
    "收款开户行",
    "金额",
    "币种",
    "用途",
    "项目",
]


def export_draft(db: Session, doc_ids: list[int], operator: str = "") -> tuple[Path, int]:
    """生成制单草稿 Excel。平台只产出草稿，银行复核/授权不在平台内完成。"""
    docs = [db.get(PaymentDoc, doc_id) for doc_id in doc_ids]
    docs = [doc for doc in docs if doc is not None and doc.status == STATUS_READY]
    if not docs:
        raise PaymentError("没有可制单的单据")

    out_dir = settings.data_dir / "payment_drafts"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"付款制单草稿_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    wb = Workbook()
    ws = wb.active
    ws.title = "付款制单草稿"
    ws.append(DRAFT_HEADERS)
    for index, doc in enumerate(docs, 1):
        ws.append(
            [
                index,
                doc.apply_no,
                doc.payer_name,
                doc.payer_account_no,
                doc.payee_name,
                doc.payee_account_no,
                doc.payee_bank,
                float(doc.amount or 0),
                doc.currency,
                doc.subject,
                doc.project_name,
            ]
        )
    ws.freeze_panes = "A2"
    wb.save(path)

    for doc in docs:
        doc.status = STATUS_DRAFTED
        doc.drafted_by = operator
        doc.drafted_at = dt.datetime.now()
        doc.draft_ref = str(path)
    db.commit()
    return path, len(docs)
