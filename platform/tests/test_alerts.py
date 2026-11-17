from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app.models import Agent, AlertEvent, BankAccount, CollectionTask, Enterprise
from app.services import alerts, app_settings


def _account(db) -> BankAccount:
    stamp = int(dt.datetime.now().timestamp() * 1000)
    enterprise = Enterprise(name=f"告警测试主体{stamp}")
    db.add(enterprise)
    db.flush()
    account = BankAccount(
        enterprise_id=enterprise.id,
        bank_code="citic",
        account_no=f"622{stamp}",
        account_name="告警测试账户",
        collect_enabled=True,
    )
    db.add(account)
    db.commit()
    return account


def test_scan_creates_failed_task_event_once(db):
    account = _account(db)
    now = dt.datetime.now()
    task = CollectionTask(
        account_id=account.id,
        type="statement",
        start_date=now.date(),
        end_date=now.date(),
        status="failed",
        attempts=3,
        error_code="TIMEOUT",
        error_msg="银行页面响应超时",
        finished_at=now,
        idempotency_key=f"alert-test-{int(now.timestamp() * 1000)}",
    )
    db.add(task)
    db.commit()

    alerts.scan(db, now)
    query = select(AlertEvent).where(
        AlertEvent.object_type == "collection_task", AlertEvent.object_id == task.id
    )
    events = db.execute(query).scalars().all()
    assert len(events) == 1
    assert "告警测试账户" in events[0].title
    assert events[0].status == "pending"

    alerts.scan(db, now)
    assert len(db.execute(query).scalars().all()) == 1


def test_scan_creates_agent_offline_event(db):
    now = dt.datetime.now()
    stamp = int(now.timestamp() * 1000)
    agent = Agent(code=f"alert-agent-{stamp}", name="告警测试设备", last_heartbeat=now - dt.timedelta(minutes=45))
    db.add(agent)
    db.commit()

    alerts.scan(db, now)
    event = db.execute(
        select(AlertEvent).where(AlertEvent.object_type == "agent", AlertEvent.object_id == agent.id)
    ).scalars().first()
    assert event is not None
    assert "告警测试设备" in event.title


def test_quiet_hours():
    assert alerts.valid_quiet_hours("")
    assert alerts.valid_quiet_hours("22:00-07:00")
    assert not alerts.valid_quiet_hours("25:00-07:00")
    assert alerts.in_quiet_hours(dt.datetime(2026, 1, 1, 23, 0), "22:00-07:00")
    assert alerts.in_quiet_hours(dt.datetime(2026, 1, 1, 6, 0), "22:00-07:00")
    assert not alerts.in_quiet_hours(dt.datetime(2026, 1, 1, 12, 0), "22:00-07:00")
    assert not alerts.in_quiet_hours(dt.datetime(2026, 1, 1, 12, 0), "")


def test_deliver_marks_event_sent(db, monkeypatch):
    calls = []

    def fake_send(url, content, timeout=8.0):
        calls.append((url, content))
        return True, ""

    monkeypatch.setattr(alerts, "send_webhook", fake_send)
    app_settings.set_value(db, "alert_enabled", "1")
    app_settings.set_value(db, "alert_webhook", "https://example.test/hook")
    app_settings.set_value(db, "alert_quiet_hours", "")

    event = AlertEvent(kind="task_failed", level="critical", title="发送测试", content="内容", status="pending")
    db.add(event)
    db.commit()

    alerts.deliver(db, dt.datetime.now())
    db.refresh(event)
    assert event.status == "sent"
    assert event.attempts == 1
    assert calls
