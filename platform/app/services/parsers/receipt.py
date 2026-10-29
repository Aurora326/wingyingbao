from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path


@dataclass
class ParsedReceipt:
    text: str = ""
    receipt_no: str = ""
    txn_date: dt.date | None = None
    amount: Decimal | None = None
    counterparty_name: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def dedupe_key(self) -> str:
        if self.receipt_no:
            return self.receipt_no
        return f"{self.txn_date or ''}|{self.amount or ''}|{self.counterparty_name}"


AMOUNT_PATTERNS = [
    r"金额[(（]?小写[)）]?\s*[:：]?\s*(?:CNY|RMB|¥)?\s*([0-9,]+\.\d{2})",
    r"(?:CNY|RMB|¥)\s?([0-9,]+\.\d{2})",
    r"(?:金额|合计|小写金额)[^\d\-]{0,8}([0-9,]+\.\d{2})",
]
DATE_PATTERNS = [
    r"交易日期\s*[:：]?\s*(\d{4})[-/年.]?(\d{1,2})[-/月.]?(\d{1,2})",
    r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})",
    r"(\d{4})(\d{2})(\d{2})",
]
RECEIPT_NO_PATTERNS = [
    r"回单编号\s*[:：]\s*([A-Za-z0-9\-]{6,40})",
    r"核心流水号\s*[:：]\s*([A-Za-z0-9\-]{6,40})",
    r"(?:流水号|凭证号|交易流水号)\s*[:：\s]*([A-Za-z0-9\-]{6,40})",
]
BLOCK_SPLIT = re.compile(r"(?=回单编号\s*[:：])")
NAME_LINE = re.compile(r"名称\s+(\S+)")


def extract_text(path: Path) -> str:
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("未安装 pdfplumber，无法解析 PDF 回单") from exc
    chunks: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            chunks.append(page.extract_text() or "")
    return "\n".join(chunks)


def _match(pattern: str, text: str) -> re.Match | None:
    return re.search(pattern, text, re.MULTILINE)


def _parse_amount(text: str) -> Decimal | None:
    for pattern in AMOUNT_PATTERNS:
        match = _match(pattern, text)
        if not match:
            continue
        try:
            return Decimal(match.group(1).replace(",", "")).quantize(Decimal("0.01"))
        except Exception:
            continue
    return None


def _parse_date(text: str) -> dt.date | None:
    for pattern in DATE_PATTERNS:
        match = _match(pattern, text)
        if not match:
            continue
        try:
            year, month, day = (int(match.group(i)) for i in (1, 2, 3))
            return dt.date(year, month, day)
        except ValueError:
            continue
    return None


def _parse_counterparty(text: str) -> str:
    match = _match(r"付款名称\s*[:：]\s*(\S.*)", text)
    if match:
        return match.group(1).strip()
    match = _match(r"付款单位\s*[:：]\s*(\S.*)", text)
    if match:
        return match.group(1).strip()
    names = NAME_LINE.findall(text)
    if names:
        return names[0].strip()
    match = _match(r"收款名称\s*[:：]\s*(\S.*)", text)
    if match:
        return match.group(1).strip()
    match = _match(r"付款银行\s*[:：]\s*(\S.*)", text)
    if match:
        return match.group(1).strip()
    return ""


def parse_block(text: str) -> ParsedReceipt:
    result = ParsedReceipt(text=text.strip())
    result.amount = _parse_amount(text)
    result.txn_date = _parse_date(text)
    result.counterparty_name = _parse_counterparty(text)[:256]
    for pattern in RECEIPT_NO_PATTERNS:
        match = _match(pattern, text)
        if match:
            result.receipt_no = match.group(1).strip()
            break
    if result.amount is None:
        result.warnings.append("未能从回单中提取金额")
    if result.txn_date is None:
        result.warnings.append("未能从回单中提取交易日期")
    return result


def parse_receipts(path: Path) -> list[ParsedReceipt]:
    path = Path(path)
    if path.suffix.lower() != ".pdf":
        text = path.read_text(encoding="utf-8", errors="ignore")[:4000]
        return [ParsedReceipt(text=text)]
    text = extract_text(path)
    if not text.strip():
        return []
    blocks = [block for block in BLOCK_SPLIT.split(text) if block.strip()]
    if len(blocks) <= 1:
        return [parse_block(text)]
    return [parse_block(block) for block in blocks]


def parse_receipt(path: Path) -> ParsedReceipt:
    parsed = parse_receipts(path)
    if parsed:
        return parsed[0]
    return ParsedReceipt()
