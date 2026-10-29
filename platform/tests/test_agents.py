from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app.config import settings
from app.models import Agent, BankAccount, CollectionTask, Enterprise, UkeyDevice
from app.services.agents import ensure_local_agent, resolve_agent_code
from app.services.executor import claim_task
from app.services.scheduler import enqueue_task


def test_seed_registers_local_agent(db):
    from app.seed import seed

    seed(db)
    agent = db.execute(select(Agent).where(Agent.code == settings.agent_code)).scalars().first()
    assert agent is not None
    assert agent.status == "online"
    assert agent.hostname
    assert agent.last_heartbeat is not None


def test_task_routes_to_device_agent(db):
    ensure_local_agent(db)
    enterprise = db.execute(select(Enterprise).order_by(Enterprise.id)).scalars().first()
    if enterprise is None:
        enterprise = Enterprise(name="路由测试企业")
        db.add(enterprise)
        db.flush()

    device = UkeyDevice(
        name="远端设备",
        bank_code="citic",
        backend="mb",
        agent_code="win-02",
        gf3_cabinet=1,
        gf3_port=2,
    )
    db.add(device)
    db.flush()
    account = BankAccount(
        enterprise_id=enterprise.id,
        bank_code="citic",
        account_no="ROUTE0001",
        account_name="路由测试账户",
        ukey_device_id=device.id,
    )
    db.add(account)
    db.commit()

    assert resolve_agent_code(db, account) == "win-02"
    task = enqueue_task(
        db, account, "statements", dt.date(2026, 2, 1), dt.date(2026, 2, 28), trigger="manual", force=True
    )
    assert task.agent_code == "win-02"

    assert claim_task(db, settings.agent_code) is None, "本机不应领到别台执行机的任务"
    claimed = claim_task(db, "win-02")
    assert claimed is not None and claimed.id == task.id


def test_account_without_device_routes_to_local(db):
    ensure_local_agent(db)
    enterprise = db.execute(select(Enterprise).order_by(Enterprise.id)).scalars().first()
    account = BankAccount(
        enterprise_id=enterprise.id,
        bank_code="mock",
        account_no="ROUTE0002",
        account_name="无设备账户",
    )
    db.add(account)
    db.commit()
    assert resolve_agent_code(db, account) == settings.agent_code


def test_devices_are_bound_to_local_agent_after_sync(db):
    from app.services.device_sync import sync_devices
    from app.services.usb_robot import parse_device
    from tests.test_usb_robot import FakeClient, _payload

    devices = [parse_device(row) for row in _payload()["response"]["usbdata"]]
    for device in devices:
        device.cabinet = 8888
    sync_devices(db, FakeClient(devices))
    rows = db.execute(select(UkeyDevice).where(UkeyDevice.gf3_cabinet == 8888)).scalars().all()
    assert len(rows) == 3
    assert all(row.agent_code == settings.agent_code for row in rows)
