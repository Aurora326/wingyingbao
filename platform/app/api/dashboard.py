from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import BalanceSnapshot, BankAccount, CollectionTask, Statement, UkeyDevice

router = APIRouter(prefix="/api", tags=["dashboard"])


def _latest_snapshots(db: Session) -> dict[int, BalanceSnapshot]:
    rows = db.execute(
        select(BalanceSnapshot).order_by(BalanceSnapshot.account_id, BalanceSnapshot.snapshot_date.desc())
    ).scalars().all()
    latest: dict[int, BalanceSnapshot] = {}
    for row in rows:
        latest.setdefault(row.account_id, row)
    return latest


@router.get("/dashboard/summary")
def summary(db: Session = Depends(get_db)):
    accounts = db.execute(select(BankAccount).order_by(BankAccount.id)).scalars().all()
    snapshots = _latest_snapshots(db)
    today = dt.date.today()

    inflow, outflow, uncleared = db.execute(
        select(
            func.coalesce(func.sum(Statement.amount).filter(Statement.direction == "credit"), 0),
            func.coalesce(func.sum(Statement.amount).filter(Statement.direction == "debit"), 0),
            func.count(Statement.id).filter(Statement.matched_internal_doc_id.is_(None)),
        ).where(Statement.txn_date == today)
    ).one()

    trend_start = today - dt.timedelta(days=6)
    trend_rows = db.execute(
        select(Statement.txn_date, Statement.direction, func.sum(Statement.amount))
        .where(Statement.txn_date >= trend_start)
        .group_by(Statement.txn_date, Statement.direction)
    ).all()
    trend_map: dict[str, dict[str, float]] = {}
    for day, direction, total in trend_rows:
        bucket = trend_map.setdefault(day.isoformat(), {"credit": 0.0, "debit": 0.0})
        bucket[direction] = float(total or 0)

    failed = db.execute(
        select(func.count(CollectionTask.id)).where(CollectionTask.status == "failed")
    ).scalar_one()
    queued = db.execute(
        select(func.count(CollectionTask.id)).where(CollectionTask.status.in_(("queued", "leased", "running")))
    ).scalar_one()

    anomalies = []
    for account in accounts:
        snapshot = snapshots.get(account.id)
        if snapshot and not snapshot.is_continuous:
            anomalies.append({"type": "balance", "account": account.account_no, "message": snapshot.note})
    for device in db.execute(select(UkeyDevice)).scalars().all():
        if device.cert_expires_at and (device.cert_expires_at - today).days <= 30:
            anomalies.append(
                {
                    "type": "cert",
                    "account": device.name,
                    "message": f"证书将于 {device.cert_expires_at} 到期",
                }
            )

    total_balance = sum((Decimal(s.balance) for s in snapshots.values()), Decimal("0"))
    return {
        "total_balance": str(total_balance),
        "account_count": len(accounts),
        "today_inflow": str(inflow or 0),
        "today_outflow": str(outflow or 0),
        "unmatched_count": int(uncleared or 0),
        "failed_tasks": failed,
        "active_tasks": queued,
        "trend": [{"date": day, **values} for day, values in sorted(trend_map.items())],
        "anomalies": anomalies[:20],
        "accounts": [
            {
                "id": a.id,
                "bank_code": a.bank_code,
                "account_no": a.account_no,
                "account_name": a.account_name,
                "balance": str(snapshots[a.id].balance) if a.id in snapshots else None,
                "balance_date": snapshots[a.id].snapshot_date if a.id in snapshots else None,
                "watermark_date": a.watermark_date,
            }
            for a in accounts
        ],
    }
