"""登录失败计数与临时锁定。

规则：同一登录账户连续失败 5 次锁 15 分钟；成功登录清除计数；
锁定期满后自动解锁，不依赖人工操作。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LoginAttempt

MAX_FAILURES = 5
LOCK_MINUTES = 15


def _row(db: Session, username: str) -> LoginAttempt | None:
    return db.execute(
        select(LoginAttempt).where(LoginAttempt.username == username)
    ).scalars().first()


def lock_remaining(db: Session, username: str, now: dt.datetime | None = None) -> int:
    """返回剩余锁定秒数，0 表示可以尝试登录。"""
    now = now or dt.datetime.now()
    row = _row(db, username)
    if row is None or row.locked_until is None:
        return 0
    remaining = (row.locked_until - now).total_seconds()
    if remaining > 0:
        return int(remaining)
    row.fail_count = 0
    row.locked_until = None
    db.commit()
    return 0


def record_failure(
    db: Session,
    username: str,
    ip: str = "",
    now: dt.datetime | None = None,
) -> tuple[int, bool]:
    """记一次失败，返回（当前失败次数，是否刚触发锁定）。"""
    now = now or dt.datetime.now()
    row = _row(db, username)
    if row is None:
        row = LoginAttempt(username=username, fail_count=0)
        db.add(row)
        db.flush()
    row.fail_count += 1
    row.last_failed_at = now
    row.last_ip = ip[:64]
    locked = row.fail_count >= MAX_FAILURES
    if locked:
        row.locked_until = now + dt.timedelta(minutes=LOCK_MINUTES)
    db.commit()
    return row.fail_count, locked


def clear_failures(db: Session, username: str) -> None:
    row = _row(db, username)
    if row is not None:
        db.delete(row)
        db.commit()
