from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InternalDoc, ReconBatch, ReconItem, Statement

COMPANY_SUFFIXES = ["有限公司", "有限责任公司", "股份有限公司", "公司", "（上海）", "(上海)"]


def normalize_name(value: str) -> str:
    text = (value or "").strip()
    text = re.sub(r"[\s（）()【】\[\]·.,，、]", "", text)
    for suffix in COMPANY_SUFFIXES:
        text = text.replace(suffix, "")
    return text


def create_batch(db: Session, period_start: dt.date, period_end: dt.date, name: str = "", created_by: str = "") -> ReconBatch:
    batch = ReconBatch(
        name=name or f"{period_start.isoformat()} ~ {period_end.isoformat()}",
        period_start=period_start,
        period_end=period_end,
        created_by=created_by,
    )
    db.add(batch)
    db.commit()
    return batch


def _score(statement: Statement, doc: InternalDoc) -> float:
    score = 0.0
    if statement.amount == doc.amount:
        score += 0.6
    if statement.direction == doc.direction:
        score += 0.2
    days = abs((statement.txn_date - doc.doc_date).days)
    if days == 0:
        score += 0.15
    elif days <= 3:
        score += 0.1
    name_a = normalize_name(statement.counterparty_name)
    name_b = normalize_name(doc.counterparty)
    if name_a and name_b and (name_a in name_b or name_b in name_a):
        score += 0.15
    return round(min(score, 1.0), 2)


def run_batch(db: Session, batch: ReconBatch) -> ReconBatch:
    db.query(ReconItem).filter(ReconItem.batch_id == batch.id).delete()
    statements = db.execute(
        select(Statement)
        .where(Statement.txn_date >= batch.period_start, Statement.txn_date <= batch.period_end)
        .order_by(Statement.txn_date, Statement.txn_time)
    ).scalars().all()
    docs = db.execute(
        select(InternalDoc).where(
            InternalDoc.doc_date >= batch.period_start - dt.timedelta(days=5),
            InternalDoc.doc_date <= batch.period_end + dt.timedelta(days=5),
            InternalDoc.status != "matched",
        )
    ).scalars().all()

    used_docs: set[int] = set()
    matched = 0
    unmatched_items = 0
    diff_total = Decimal("0")

    for statement in statements:
        candidates = [
            (_score(statement, doc), doc)
            for doc in docs
            if doc.id not in used_docs and doc.direction == statement.direction
        ]
        candidates.sort(key=lambda pair: pair[0], reverse=True)

        best = candidates[0] if candidates else (0.0, None)
        exact = [
            (score, doc)
            for score, doc in candidates
            if doc.amount == statement.amount and abs((statement.txn_date - doc.doc_date).days) == 0 and score >= 0.9
        ]
        chosen = exact[0][1] if exact else (best[1] if best[0] >= 0.75 else None)

        if chosen is not None:
            used_docs.add(chosen.id)
            matched += 1
            db.add(
                ReconItem(
                    batch_id=batch.id,
                    statement_id=statement.id,
                    internal_doc_id=chosen.id,
                    match_type="exact" if exact else "fuzzy",
                    confidence=best[0] if not exact else exact[0][0],
                    diff_amount=statement.amount - chosen.amount,
                    status="matched",
                    candidates=[
                        {"internal_doc_id": doc.id, "score": score, "amount": str(doc.amount), "counterparty": doc.counterparty}
                        for score, doc in candidates[:3]
                    ],
                )
            )
            statement.matched_internal_doc_id = chosen.id
            chosen.status = "matched"
        else:
            unmatched_items += 1
            gap = statement.amount
            potential = [doc for doc in docs if doc.id not in used_docs and doc.direction == statement.direction]
            nearest = sorted(potential, key=lambda doc: abs(doc.amount - statement.amount))[:3]
            db.add(
                ReconItem(
                    batch_id=batch.id,
                    statement_id=statement.id,
                    internal_doc_id=None,
                    match_type="none",
                    confidence=round(best[0], 2),
                    diff_amount=gap,
                    status="unmatched",
                    candidates=[
                        {"internal_doc_id": doc.id, "amount": str(doc.amount), "counterparty": doc.counterparty, "doc_no": doc.doc_no}
                        for doc in nearest
                    ],
                )
            )

    for doc in docs:
        if doc.id in used_docs:
            continue
        db.add(
            ReconItem(
                batch_id=batch.id,
                statement_id=None,
                internal_doc_id=doc.id,
                match_type="none",
                confidence=0.0,
                diff_amount=doc.amount,
                status="unmatched",
                candidates=[],
            )
        )
        diff_total += doc.amount

    batch.matched_count = matched
    batch.unmatched_count = unmatched_items
    batch.diff_amount = diff_total
    batch.status = "done"
    db.commit()
    return batch


def confirm_item(db: Session, item: ReconItem, internal_doc_id: int | None = None) -> ReconItem:
    if internal_doc_id:
        item.internal_doc_id = internal_doc_id
    if item.statement_id and item.internal_doc_id:
        statement = db.get(Statement, item.statement_id)
        doc = db.get(InternalDoc, item.internal_doc_id)
        if statement and doc:
            statement.matched_internal_doc_id = doc.id
            doc.status = "matched"
            item.status = "matched"
            item.match_type = "manual"
            item.diff_amount = statement.amount - doc.amount
    db.commit()
    return item


def ignore_item(db: Session, item: ReconItem) -> ReconItem:
    item.status = "ignored"
    db.commit()
    return item
