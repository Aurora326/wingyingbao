from __future__ import annotations

import datetime as dt
import threading

from sqlalchemy import select

from app.db import SessionLocal
from app.models import BankAccount, CollectionTask
from app.services.agents import resolve_agent_code


def _field_matches(field: str, value: int) -> bool:
    field = field.strip()
    if field == "*":
        return True
    if field.startswith("*/"):
        step = int(field[2:] or "1")
        return step > 0 and value % step == 0
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            low, high = part.split("-", 1)
            if int(low) <= value <= int(high):
                return True
        elif part.isdigit() and int(part) == value:
            return True
    return False


def cron_matches(expr: str, when: dt.datetime) -> bool:
    fields = (expr or "").split()
    if len(fields) != 5:
        return False
    minute, hour, day, month, weekday = fields
    return (
        _field_matches(minute, when.minute)
        and _field_matches(hour, when.hour)
        and _field_matches(day, when.day)
        and _field_matches(month, when.month)
        and _field_matches(weekday, when.isoweekday() % 7)
    )


# 银行对单次查询跨度有限制（工行 6 个月），水位不推进时窗口会一直变长
MAX_QUERY_DAYS = 180


def task_range(account: BankAccount, today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    today = today or dt.date.today()
    if account.watermark_date:
        start = account.watermark_date - dt.timedelta(days=3)
    else:
        start = today - dt.timedelta(days=account.days_back or 30)
    floor = today - dt.timedelta(days=MAX_QUERY_DAYS)
    if start < floor:
        start = floor
    return start, today


def enqueue_task(
    db,
    account: BankAccount,
    task_type: str,
    start_date: dt.date,
    end_date: dt.date,
    trigger: str = "schedule",
    created_by: str = "system",
    force: bool = False,
) -> CollectionTask:
    key = f"{account.id}:{task_type}:{start_date.isoformat()}:{end_date.isoformat()}"
    existing = db.execute(select(CollectionTask).where(CollectionTask.idempotency_key == key)).scalars().first()
    if existing and not force:
        return existing
    if existing and force:
        key = f"{key}:{dt.datetime.now().strftime('%H%M%S%f')}"
    task = CollectionTask(
        account_id=account.id,
        type=task_type,
        start_date=start_date,
        end_date=end_date,
        trigger=trigger,
        status="queued",
        idempotency_key=key,
        created_by=created_by,
        device_id=account.ukey_device_id,
        agent_code=resolve_agent_code(db, account),
    )
    db.add(task)
    db.commit()
    return task


class Scheduler(threading.Thread):
    def __init__(self, interval: float = 30.0) -> None:
        super().__init__(name="collector-scheduler", daemon=True)
        self.interval = interval
        self._stop = threading.Event()
        self._last_slot: dict[int, str] = {}

    def stop(self) -> None:
        self._stop.set()

    def tick(self) -> None:
        now = dt.datetime.now()
        slot = now.strftime("%Y%m%d%H%M")
        with SessionLocal() as db:
            accounts = db.execute(select(BankAccount).where(BankAccount.collect_enabled.is_(True))).scalars().all()
            for account in accounts:
                if not cron_matches(account.collect_cron, now):
                    continue
                if self._last_slot.get(account.id) == slot:
                    continue
                self._last_slot[account.id] = slot
                start, end = task_range(account, now.date())
                for task_type in ("statements", "receipts"):
                    enqueue_task(db, account, task_type, start, end, trigger="schedule", created_by="scheduler")

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                pass
            self._stop.wait(self.interval)
