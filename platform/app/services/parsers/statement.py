from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.services.parsers.tabular import read_rows

ALIASES: dict[str, list[str]] = {
    "txn_date": ["交易日期", "记账日期", "交易日", "日期", "入账日期", "交易时间"],
    "txn_time": ["交易时间", "时间"],
    "account": ["账号", "银行账号", "账户号", "卡号"],
    "counterparty_account": ["对方账号", "对方帐号", "收付方账号", "对方账户"],
    "counterparty_name": ["对方账户名称", "对方户名", "对方名称", "收(付)方名称", "对方单位名称", "对方单位"],
    "counterparty_bank": ["对方账号开户网点名称", "对方开户行", "对方行名", "开户网点", "对方开户机构", "对方银行"],
    "debit": ["借方发生额", "借方金额", "支出金额", "付款金额", "支出", "转出金额"],
    "credit": ["贷方发生额", "贷方金额", "收入金额", "收款金额", "收入", "转入金额"],
    "balance": ["账户余额", "余额"],
    "summary": ["摘要", "用途", "附言", "交易摘要"],
    "serial_no": ["柜员交易号", "唯一流水编号", "流水号", "交易流水号", "凭证号", "业务编号", "网银流水号"],
}

META_ACCOUNT = ["账号", "账户", "银行账号"]


@dataclass
class StatementRow:
    txn_date: dt.date
    txn_time: str
    direction: str
    amount: Decimal
    balance: Decimal | None
    counterparty_name: str = ""
    counterparty_account: str = ""
    counterparty_bank: str = ""
    summary: str = ""
    serial_no: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class ParsedStatement:
    rows: list[StatementRow]
    account_no: str = ""
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    warnings: list[str] = field(default_factory=list)


def _to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _to_decimal(value) -> Decimal | None:
    text = _to_text(value)
    if not text:
        return None
    text = text.replace(",", "").replace("，", "").replace(" ", "")
    text = text.replace("+", "")
    if text in ("-", "--"):
        return None
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _to_time(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dt.datetime):
        return value.strftime("%H:%M:%S")
    text = str(value).strip()
    if not text:
        return ""
    match = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    if match:
        hour, minute, second = match.group(1), match.group(2), match.group(3) or "00"
        return f"{int(hour):02d}:{minute}:{second}"
    return text[:8]


def _to_date(value) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return (dt.datetime(1899, 12, 30) + dt.timedelta(days=float(value))).date()
        except Exception:
            return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y.%m.%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    match = re.search(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", text)
    if match:
        try:
            return dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    return None


def _header_index(header: list[str]) -> dict[str, int]:
    index: dict[str, int] = {}
    normalized = [h.replace(" ", "").replace("\n", "") for h in header]
    for key, names in ALIASES.items():
        for name in names:
            target = name.replace(" ", "")
            for position, cell in enumerate(normalized):
                if cell == target or (target in cell and len(cell) <= len(target) + 6):
                    index[key] = position
                    break
            if key in index:
                break
    return index


def find_header(rows: list[list]) -> tuple[int, dict[str, int]]:
    best: tuple[int, dict[str, int]] = (-1, {})
    for position, row in enumerate(rows[:60]):
        cells = [_to_text(c) for c in row]
        index = _header_index(cells)
        if "txn_date" in index and ("debit" in index or "credit" in index) and len(index) >= 4:
            return position, index
        if len(index) > len(best[1]):
            best = (position, index)
    if best[0] >= 0 and "txn_date" in best[1]:
        return best
    raise ValueError("未能识别流水表头")


def parse_statement(path: Path) -> ParsedStatement:
    rows = read_rows(Path(path))
    if not rows:
        raise ValueError("文件为空")
    header_pos, index = find_header(rows)
    header = [_to_text(c) for c in rows[header_pos]]

    parsed = ParsedStatement(rows=[])
    for row in rows[:header_pos]:
        cells = [_to_text(c) for c in row]
        for position, cell in enumerate(cells):
            if cell in META_ACCOUNT and position + 1 < len(cells):
                value = cells[position + 1]
                if value and not parsed.account_no:
                    parsed.account_no = value
            if cell in ("起始日期", "开始日期") and position + 1 < len(cells):
                parsed.start_date = _to_date(cells[position + 1])
            if cell in ("截止日期", "结束日期") and position + 1 < len(cells):
                parsed.end_date = _to_date(cells[position + 1])

    for row in rows[header_pos + 1 :]:
        if not row or all(c in (None, "") for c in row):
            continue
        cells = list(row) + [None] * max(0, len(header) - len(row))

        def cell(key: str):
            position = index.get(key)
            return cells[position] if position is not None and position < len(cells) else None

        txn_date = _to_date(cell("txn_date"))
        if txn_date is None:
            continue
        debit = _to_decimal(cell("debit"))
        credit = _to_decimal(cell("credit"))
        if debit is None and credit is None:
            continue
        if debit is not None and debit != 0:
            direction, amount = "debit", abs(debit)
        else:
            direction, amount = "credit", abs(credit or Decimal("0"))
        if not parsed.account_no:
            parsed.account_no = _to_text(cell("account"))

        parsed.rows.append(
            StatementRow(
                txn_date=txn_date,
                txn_time=_to_time(cell("txn_time")),
                direction=direction,
                amount=amount,
                balance=_to_decimal(cell("balance")),
                counterparty_name=_to_text(cell("counterparty_name"))[:256],
                counterparty_account=_to_text(cell("counterparty_account"))[:64],
                counterparty_bank=_to_text(cell("counterparty_bank"))[:128],
                summary=_to_text(cell("summary"))[:256],
                serial_no=_to_text(cell("serial_no"))[:64],
                raw={header[i]: _to_text(cells[i]) for i in range(len(header)) if header[i]},
            )
        )
    return parsed


def dedupe_hash(bank_code: str, account_no: str, row: StatementRow) -> str:
    if row.serial_no:
        payload = f"{bank_code}|{account_no}|{row.serial_no}"
    else:
        payload = "|".join(
            [
                bank_code,
                account_no,
                row.txn_date.isoformat(),
                row.txn_time,
                row.direction,
                str(row.amount),
                str(row.balance or ""),
                row.summary,
            ]
        )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
