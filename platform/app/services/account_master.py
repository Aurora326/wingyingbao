from __future__ import annotations

import re
from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BankAccount, BookSet, Enterprise, SubAccount
from app.services.payment import _book_set_for


BANK_CODE_MAP = {
    "建设银行": "ccb",
    "上海银行": "bosh",
    "农业银行": "abc",
    "上海农商银行": "shrcb",
    "农商银行": "shrcb",
    "招商银行": "cmb",
    "中国银行": "boc",
}


class AccountMasterError(Exception):
    pass


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _bank_code(value: str) -> str:
    for key, code in BANK_CODE_MAP.items():
        if key in value:
            return code
    return ""


def _clean_account(value: str) -> tuple[str, bool, bool, bool]:
    raw = _text(value)
    has_ukey = "无盾" not in raw
    has_sub = "有子账户" in raw
    parent_hint = "挂一般户下面" in raw
    clean = re.sub(r"[（(][^）)]*[）)]", "", raw)
    clean = clean.replace(" ", "").replace("　", "")
    return clean, has_ukey, has_sub, parent_hint


def _account_type(value: str) -> tuple[str, str]:
    raw = _text(value)
    if "基本户" in raw:
        return "基本户", "社保公积金扣款" if "社保" in raw else ""
    if "贷款户" in raw:
        return "贷款户", ""
    if "监管户" in raw:
        return "监管户", ""
    if "一般户" in raw:
        return "一般户", ""
    return "一般户", ""


def _read_sheet(rows, required: str) -> list[dict[str, object]]:
    for index, row in enumerate(rows[:10]):
        headers = [_text(cell) for cell in row]
        if required in headers:
            result = []
            for raw in rows[index + 1:]:
                if not raw or all(cell in (None, "") for cell in raw):
                    continue
                result.append({headers[i]: raw[i] for i in range(min(len(headers), len(raw)))})
            return result
    return []


def _enterprise(db: Session, name: str) -> tuple[Enterprise, bool]:
    enterprise = db.execute(select(Enterprise).where(Enterprise.name == name)).scalars().first()
    if enterprise is not None:
        return enterprise, False
    enterprise = Enterprise(name=name)
    db.add(enterprise)
    db.flush()
    return enterprise, True


def import_account_master(db: Session, path: Path) -> dict:
    wb = load_workbook(path, data_only=True)
    first = wb.worksheets[0]
    rows = list(first.iter_rows(values_only=True))
    accounts = _read_sheet(rows, "单位名称")
    if not accounts:
        raise AccountMasterError("未找到「单位名称」表头")

    created = updated = enterprises_created = 0
    account_index: dict[tuple[str, str], BankAccount] = {}
    pending_parent_hints: list[BankAccount] = []

    for row in accounts:
        company = _text(row.get("单位名称"))
        raw_no = row.get("银行账户号码")
        bank = _text(row.get("开户行"))
        usage = _text(row.get("用途"))
        if not company or not raw_no:
            continue
        account_no, has_ukey, has_sub, parent_hint = _clean_account(raw_no)
        bank_code = _bank_code(bank)
        if not account_no or not bank_code:
            continue
        account_type, usage_tag = _account_type(usage)
        enterprise, ent_created = _enterprise(db, company)
        enterprises_created += 1 if ent_created else 0
        _book_set_for(db, enterprise)

        existing = db.execute(
            select(BankAccount).where(
                BankAccount.bank_code == bank_code,
                BankAccount.account_no == account_no,
            )
        ).scalars().first()
        if existing is None:
            existing = BankAccount(
                enterprise_id=enterprise.id,
                bank_code=bank_code,
                account_no=account_no,
                account_name=company,
                collect_enabled=True,
            )
            db.add(existing)
            db.flush()
            created += 1
        else:
            updated += 1

        existing.account_type = account_type
        existing.usage_tag = usage_tag or existing.usage_tag
        existing.payment_enabled = account_type in ("基本户", "一般户")
        existing.auto_voucher_enabled = account_type in ("基本户", "一般户")
        existing.has_ukey = has_ukey
        existing.has_sub_accounts = has_sub
        existing.account_status = "正常"
        account_index[(bank_code, account_no)] = existing
        if parent_hint:
            pending_parent_hints.append(existing)

    # “挂一般户下面”的账户，挂到同公司同银行的一般户下
    for account in pending_parent_hints:
        parent = db.execute(
            select(BankAccount)
            .where(
                BankAccount.enterprise_id == account.enterprise_id,
                BankAccount.bank_code == account.bank_code,
                BankAccount.account_type == "一般户",
                BankAccount.id != account.id,
            )
            .order_by(BankAccount.id)
            .limit(1)
        ).scalars().first()
        if parent is not None:
            account.parent_account_id = parent.id

    sub_created = sub_updated = 0
    sub_sheet = next((ws for ws in wb.worksheets if "账簿" in ws.title or "子账户" in ws.title), None)
    if sub_sheet is not None:
        sub_rows = _read_sheet(list(sub_sheet.iter_rows(values_only=True)), "账簿名")
        for row in sub_rows:
            ledger_name = _text(row.get("账簿名"))
            sub_no = _text(row.get("账簿号"))
            if not ledger_name or not sub_no:
                continue
            if sub_no in ("0000000000", "9999999999"):
                continue
            companies = sorted({a.account_name for a in account_index.values()}, key=len, reverse=True)
            company = next((name for name in companies if name and ledger_name.startswith(name)), "")
            parent = None
            if company:
                parent = db.execute(
                    select(BankAccount)
                    .where(
                        BankAccount.account_name == company,
                        BankAccount.has_sub_accounts.is_(True),
                    )
                    .order_by(BankAccount.id)
                    .limit(1)
                ).scalars().first()
            if parent is None:
                continue
            project_name = ledger_name[len(company):].strip() if company and ledger_name.startswith(company) else ledger_name
            sub = db.execute(
                select(SubAccount).where(
                    SubAccount.parent_account_id == parent.id,
                    SubAccount.sub_no == sub_no,
                )
            ).scalars().first()
            if sub is None:
                sub = SubAccount(parent_account_id=parent.id, sub_no=sub_no)
                db.add(sub)
                sub_created += 1
            else:
                sub_updated += 1
            sub.ledger_name = ledger_name
            sub.project_name = project_name
            sub.currency = _text(row.get("币种")) or "人民币"
            sub.status = "正常"

    db.commit()
    return {
        "accounts_created": created,
        "accounts_updated": updated,
        "sub_accounts_created": sub_created,
        "sub_accounts_updated": sub_updated,
        "enterprises_created": enterprises_created,
    }


def account_master_summary(db: Session) -> dict:
    accounts = [a for a in db.execute(select(BankAccount)).scalars().all() if a.account_type]
    subs = db.execute(select(SubAccount)).scalars().all()
    by_type: dict[str, int] = {}
    for account in accounts:
        key = account.account_type or "未分类"
        by_type[key] = by_type.get(key, 0) + 1
    return {
        "total": len(accounts),
        "payable": sum(1 for a in accounts if a.payment_enabled),
        "loan": sum(1 for a in accounts if a.account_type == "贷款户"),
        "supervised": sum(1 for a in accounts if a.account_type == "监管户"),
        "sub_accounts": len(subs),
        "by_type": by_type,
    }
