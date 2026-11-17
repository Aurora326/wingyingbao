from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app.models import LoginAttempt
from app.services import login_guard, security


def test_validate_password_policy():
    assert security.validate_password("Finance2026") == ""
    assert "8 个字符" in security.validate_password("short1")
    assert "字母" in security.validate_password("12345678")
    assert "弱密码" in security.validate_password("admin123")
    assert "数字" in security.validate_password("abcdefgh")
    assert "字母" in security.validate_password("87654321")
    assert "相同字符" in security.validate_password("aaaaaaaa")
    assert "登录账户相同" in security.validate_password("tester123", "tester123")


def test_default_password_detection():
    hashed = security.hash_password("admin123")
    assert security.is_default_password(security.settings.admin_user, hashed)
    other = security.hash_password("StrongPass123")
    assert not security.is_default_password(security.settings.admin_user, other)
    assert not security.is_default_password("someone", hashed)


def test_lockout_after_failures(db):
    now = dt.datetime(2026, 1, 1, 10, 0, 0)
    for _ in range(4):
        count, locked = login_guard.record_failure(db, "lock-test", "127.0.0.1", now=now)
        assert locked is False
    count, locked = login_guard.record_failure(db, "lock-test", "127.0.0.1", now=now)
    assert count == 5
    assert locked is True

    remaining = login_guard.lock_remaining(db, "lock-test", now=now)
    assert 0 < remaining <= login_guard.LOCK_MINUTES * 60

    later = now + dt.timedelta(minutes=login_guard.LOCK_MINUTES + 1)
    assert login_guard.lock_remaining(db, "lock-test", now=later) == 0
    row = db.execute(
        select(LoginAttempt).where(LoginAttempt.username == "lock-test")
    ).scalars().first()
    db.refresh(row)
    assert row.fail_count == 0
    assert row.locked_until is None


def test_clear_failures_after_success(db):
    login_guard.record_failure(db, "clear-test", "10.0.0.8")
    assert login_guard.lock_remaining(db, "clear-test") == 0
    login_guard.clear_failures(db, "clear-test")
    row = db.execute(
        select(LoginAttempt).where(LoginAttempt.username == "clear-test")
    ).scalars().first()
    assert row is None
