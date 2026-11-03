from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BalanceSnapshot, BankAccount, Receipt, Statement
from app.services.parsers.receipt import ParsedReceipt, parse_receipts
from app.services.parsers.statement import dedupe_hash, parse_statement


@dataclass
class IngestResult:
    inserted: int = 0
    duplicates: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "inserted": self.inserted,
            "duplicates": self.duplicates,
            "failed": self.failed,
            "errors": self.errors,
        }


def ingest_statement(db: Session, account: BankAccount, path: Path, artifact_id: int | None) -> IngestResult:
    result = IngestResult()
    try:
        parsed = parse_statement(Path(path))
    except Exception as exc:  # noqa: BLE001
        result.failed += 1
        result.errors.append(f"{Path(path).name}: {exc}")
        return result

    existing = {
        row[0]
        for row in db.execute(select(Statement.dedupe_hash).where(Statement.account_id == account.id)).all()
    }
    for row in parsed.rows:
        key = dedupe_hash(account.bank_code, account.account_no, row)
        if key in existing:
            result.duplicates += 1
            continue
        existing.add(key)
        db.add(
            Statement(
                account_id=account.id,
                artifact_id=artifact_id,
                txn_date=row.txn_date,
                txn_time=row.txn_time,
                direction=row.direction,
                amount=row.amount,
                balance=row.balance,
                counterparty_name=row.counterparty_name,
                counterparty_account=row.counterparty_account,
                counterparty_bank=row.counterparty_bank,
                summary=row.summary,
                serial_no=row.serial_no,
                dedupe_hash=key,
                raw_json=row.raw,
                tenant_id=account.tenant_id,
            )
        )
        result.inserted += 1
    db.flush()
    return result


def _receipt_exists(db: Session, account: BankAccount, object_key: str, parsed: ParsedReceipt) -> bool:
    query = select(Receipt.id).where(Receipt.account_id == account.id, Receipt.object_key == object_key)
    if parsed.receipt_no:
        query = query.where(Receipt.receipt_no == parsed.receipt_no)
    else:
        query = query.where(Receipt.receipt_no == "")
        query = query.where(Receipt.amount.is_(None) if parsed.amount is None else Receipt.amount == parsed.amount)
        query = query.where(Receipt.txn_date.is_(None) if parsed.txn_date is None else Receipt.txn_date == parsed.txn_date)
    return db.execute(query).scalars().first() is not None


def ingest_receipt(db: Session, account: BankAccount, path: Path, artifact_id: int | None, object_key: str) -> IngestResult:
    result = IngestResult()
    path = Path(path)
    try:
        parsed_list = parse_receipts(path)
    except Exception as exc:  # noqa: BLE001
        result.failed += 1
        result.errors.append(f"{path.name}: {exc}")
        existing = db.execute(
            select(Receipt.id).where(Receipt.account_id == account.id, Receipt.object_key == object_key)
        ).scalars().first()
        if existing is None:
            db.add(
                Receipt(
                    account_id=account.id,
                    artifact_id=artifact_id,
                    object_key=object_key,
                    extracted_text="",
                    tenant_id=account.tenant_id,
                )
            )
        return result

    if not parsed_list:
        result.failed += 1
        result.errors.append(f"{path.name}: 未提取到回单内容")
        return result

    for parsed in parsed_list:
        if _receipt_exists(db, account, object_key, parsed):
            result.duplicates += 1
            continue
        receipt = Receipt(
            account_id=account.id,
            artifact_id=artifact_id,
            receipt_no=parsed.receipt_no,
            txn_date=parsed.txn_date,
            amount=parsed.amount,
            counterparty_name=parsed.counterparty_name,
            object_key=object_key,
            extracted_text=parsed.text[:20000],
            tenant_id=account.tenant_id,
        )
        db.add(receipt)
        db.flush()
        result.inserted += 1

        if parsed.amount is None or parsed.txn_date is None:
            continue
        window_start = parsed.txn_date - dt.timedelta(days=3)
        window_end = parsed.txn_date + dt.timedelta(days=3)
        candidate = db.execute(
            select(Statement)
            .where(
                Statement.account_id == account.id,
                Statement.amount == parsed.amount,
                Statement.txn_date >= window_start,
                Statement.txn_date <= window_end,
                Statement.matched_receipt_id.is_(None),
            )
            .order_by(Statement.txn_date)
        ).scalars().first()
        if candidate:
            receipt.matched_statement_id = candidate.id
            candidate.matched_receipt_id = receipt.id
    return result


def verify_balance_continuity(db: Session, account_id: int) -> tuple[bool, list[str]]:
    account = db.get(BankAccount, account_id)
    rows = db.execute(
        select(Statement)
        .where(Statement.account_id == account_id)
        .order_by(Statement.txn_date, Statement.txn_time, Statement.id)
    ).scalars().all()
    breaks: list[str] = []
    for previous, current in zip(rows, rows[1:]):
        if previous.balance is None or current.balance is None:
            continue
        signed = current.amount if current.direction == "credit" else -current.amount
        expected = (previous.balance + signed).quantize(Decimal("0.01"))
        if abs(expected - current.balance) > Decimal("0.01"):
            breaks.append(
                f"{current.txn_date} {current.txn_time} 余额不连续: 上笔 {previous.balance} "
                f"{'+' if current.direction == 'credit' else '-'}{current.amount} != {current.balance}"
            )
    if rows:
        latest = rows[-1]
        if latest.balance is not None:
            snapshot = db.execute(
                select(BalanceSnapshot).where(
                    BalanceSnapshot.account_id == account_id,
                    BalanceSnapshot.snapshot_date == latest.txn_date,
                )
            ).scalars().first()
            if snapshot is None:
                snapshot = BalanceSnapshot(
                    account_id=account_id,
                    snapshot_date=latest.txn_date,
                    balance=latest.balance,
                    source="statement",
                    tenant_id=account.tenant_id if account else None,
                )
                db.add(snapshot)
            else:
                snapshot.balance = latest.balance
            snapshot.is_continuous = not breaks
            snapshot.note = breaks[0][:250] if breaks else ""
    return not breaks, breaks
