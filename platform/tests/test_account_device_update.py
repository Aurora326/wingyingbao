from __future__ import annotations

import datetime as dt

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.accounts import update_account, update_account_device
from app.models import BankAccount, CollectionTask, Credential, Enterprise, UkeyDevice
from app.schemas import AccountDeviceUpdate, AccountUpdate
from app.services import security
from app.services.scheduler import enqueue_task


def _account(db, account_no: str, device: UkeyDevice) -> BankAccount:
    enterprise = Enterprise(name=f"绑定测试-{account_no}")
    db.add(enterprise)
    db.flush()
    account = BankAccount(
        enterprise_id=enterprise.id,
        bank_code="mock",
        account_no=account_no,
        account_name="绑定测试账户",
        ukey_device_id=device.id,
        days_back=30,
    )
    db.add(account)
    db.commit()
    return account


def _device(db, name: str, port: int, agent: str, bank: str = "mock") -> UkeyDevice:
    device = UkeyDevice(
        name=name,
        bank_code=bank,
        backend="mock",
        agent_code=agent,
        gf3_cabinet=900,
        gf3_port=port,
        status="online",
    )
    db.add(device)
    db.commit()
    return device


def test_update_account_device_moves_queued_task(db):
    d1 = _device(db, "端口11设备", 11, "agent-a")
    d2 = _device(db, "端口12设备", 12, "agent-b")
    account = _account(db, "BIND-API-0001", d1)
    task = enqueue_task(
        db, account, "statements", dt.date(2026, 10, 1), dt.date(2026, 10, 2),
        trigger="manual", created_by="test",
    )

    result = update_account_device(account.id, AccountDeviceUpdate(ukey_device_id=d2.id), db)
    assert result["device_id"] == d2.id
    assert result["queued_tasks"] == 1
    db.expire_all()
    assert db.get(BankAccount, account.id).ukey_device_id == d2.id
    queued = db.get(CollectionTask, task.id)
    assert queued.device_id == d2.id
    assert queued.agent_code == "agent-b"


def test_update_account_device_rejects_wrong_bank(db):
    d1 = _device(db, "端口21设备", 21, "agent-a")
    other_bank = _device(db, "工行设备", 22, "agent-c", bank="icbc")
    account = _account(db, "BIND-API-0002", d1)

    with pytest.raises(HTTPException) as exc:
        update_account_device(account.id, AccountDeviceUpdate(ukey_device_id=other_bank.id), db)
    assert exc.value.status_code == 400


def test_update_account_device_rejects_occupied_device(db):
    d1 = _device(db, "端口31设备", 31, "agent-a")
    d2 = _device(db, "端口32设备", 32, "agent-b")
    first = _account(db, "BIND-API-0003", d1)
    _account(db, "BIND-API-0004", d2)

    with pytest.raises(HTTPException) as exc:
        update_account_device(first.id, AccountDeviceUpdate(ukey_device_id=d2.id), db)
    assert exc.value.status_code == 400


def test_update_account_device_blocks_active_task(db):
    d1 = _device(db, "端口41设备", 41, "agent-a")
    d2 = _device(db, "端口42设备", 42, "agent-b")
    account = _account(db, "BIND-API-0005", d1)
    task = enqueue_task(
        db, account, "statements", dt.date(2026, 10, 1), dt.date(2026, 10, 2),
        trigger="manual", created_by="test",
    )
    task.status = "leased"
    db.commit()

    with pytest.raises(HTTPException) as exc:
        update_account_device(account.id, AccountDeviceUpdate(ukey_device_id=d2.id), db)
    assert exc.value.status_code == 409


def test_update_account_changes_schedule_and_name(db):
    d1 = _device(db, "端口51设备", 51, "agent-a")
    account = _account(db, "BIND-API-0006", d1)
    result = update_account(
        account.id,
        AccountUpdate(account_name="新户名", collect_cron="0 9 * * *", days_back=45),
        db,
    )
    assert result["ok"] is True
    db.expire_all()
    updated = db.get(BankAccount, account.id)
    assert updated.account_name == "新户名"
    assert updated.collect_cron == "0 9 * * *"
    assert updated.days_back == 45


def test_update_account_keeps_credentials_when_blank(db):
    d1 = _device(db, "端口61设备", 61, "agent-a")
    account = _account(db, "BIND-API-0007", d1)
    db.add(
        Credential(
            bank_account_id=account.id,
            username_enc=security.encrypt("old-user"),
            password_enc=security.encrypt("old-pass"),
            pin_enc=security.encrypt("old-pin"),
        )
    )
    db.commit()

    update_account(account.id, AccountUpdate(username="new-user"), db)
    db.expire_all()
    credential = db.execute(
        select(Credential).where(Credential.bank_account_id == account.id)
    ).scalars().first()
    assert security.decrypt(credential.username_enc) == "new-user"
    assert security.decrypt(credential.password_enc) == "old-pass"
    assert security.decrypt(credential.pin_enc) == "old-pin"
