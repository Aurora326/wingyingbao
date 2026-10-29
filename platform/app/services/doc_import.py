from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import InternalDoc
from app.services.parsers.statement import _to_date, _to_decimal, _to_text, find_header
from app.services.parsers.tabular import read_rows

DOC_ALIASES = {
    "amount": ["金额", "应收金额", "应付金额", "合计", "发生额"],
    "direction": ["方向", "收付", "收付方向", "借贷"],
    "counterparty": ["对方", "往来单位", "客户", "供应商", "对方单位", "单位名称"],
    "doc_date": ["日期", "单据日期", "业务日期", "发生日期"],
    "doc_no": ["单号", "单据号", "合同号", "发票号", "凭证号"],
    "type": ["类型", "单据类型", "业务类型"],
}

DIRECTION_MAP = {
    "收": "credit",
    "收入": "credit",
    "收款": "credit",
    "贷": "credit",
    "进账": "credit",
    "付": "debit",
    "支出": "debit",
    "付款": "debit",
    "借": "debit",
    "出账": "debit",
}


def _index(header: list[str]) -> dict[str, int]:
    normalized = [h.replace(" ", "") for h in header]
    found: dict[str, int] = {}
    for key, names in DOC_ALIASES.items():
        for name in names:
            target = name.replace(" ", "")
            for position, cell in enumerate(normalized):
                if cell == target or (target in cell and len(cell) <= len(target) + 4):
                    found[key] = position
                    break
            if key in found:
                break
    return found


def import_docs(db: Session, enterprise_id: int, path: Path, batch: str = "") -> tuple[int, int]:
    rows = read_rows(Path(path))
    if not rows:
        return 0, 0
    try:
        header_pos, _ = find_header(rows)
    except ValueError:
        header_pos = 0
    for position in range(0, min(len(rows), 20)):
        candidate = _index([_to_text(c) for c in rows[position]])
        if "amount" in candidate and ("doc_date" in candidate or "counterparty" in candidate):
            header_pos = position
            index = candidate
            break
    else:
        return 0, 0

    inserted = skipped = 0
    for row in rows[header_pos + 1 :]:
        if not row or all(c in (None, "") for c in row):
            continue

        def cell(key: str):
            position = index.get(key)
            return row[position] if position is not None and position < len(row) else None

        amount = _to_decimal(cell("amount"))
        doc_date = _to_date(cell("doc_date"))
        if amount is None or doc_date is None:
            skipped += 1
            continue
        direction_text = _to_text(cell("direction"))
        direction = DIRECTION_MAP.get(direction_text, "credit")
        db.add(
            InternalDoc(
                enterprise_id=enterprise_id,
                type=_to_text(cell("type"))[:16] or "other",
                doc_no=_to_text(cell("doc_no"))[:64],
                amount=amount,
                direction=direction,
                counterparty=_to_text(cell("counterparty"))[:256],
                doc_date=doc_date,
                source="import",
                imported_batch=batch,
            )
        )
        inserted += 1
    db.commit()
    return inserted, skipped
