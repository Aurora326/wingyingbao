from __future__ import annotations

import datetime as dt

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Agent, BankAccount, BalanceSnapshot, Credential, Enterprise, UkeyDevice
from app.schemas import AccountCreate, CredentialUpdate, DeviceCreate
from app.services import account_admin, device_sync, security
from app.services.scheduler import enqueue_task, task_range
from app.services.usb_robot import UsbRobotError

router = APIRouter(prefix="/api", tags=["accounts"])


@router.get("/accounts")
def list_accounts(db: Session = Depends(get_db)):
    accounts = db.execute(select(BankAccount).order_by(BankAccount.id)).scalars().all()
    items = []
    for account in accounts:
        snapshot = db.execute(
            select(BalanceSnapshot)
            .where(BalanceSnapshot.account_id == account.id)
            .order_by(BalanceSnapshot.snapshot_date.desc())
            .limit(1)
        ).scalars().first()
        device = db.get(UkeyDevice, account.ukey_device_id) if account.ukey_device_id else None
        items.append(
            {
                "id": account.id,
                "bank_code": account.bank_code,
                "account_no": account.account_no,
                "account_name": account.account_name,
                "collect_enabled": account.collect_enabled,
                "collect_cron": account.collect_cron,
                "days_back": account.days_back,
                "watermark_date": account.watermark_date,
                "balance": str(snapshot.balance) if snapshot else None,
                "balance_date": snapshot.snapshot_date if snapshot else None,
                "device": device.name if device else "",
                "device_id": device.id if device else None,
                **account_admin.account_stats(db, account.id),
            }
        )
    return {"items": items}


@router.post("/accounts")
def create_account(payload: AccountCreate, db: Session = Depends(get_db)):
    enterprise = db.execute(select(Enterprise).order_by(Enterprise.id)).scalars().first()
    if enterprise is None:
        enterprise = Enterprise(name="默认企业")
        db.add(enterprise)
        db.flush()
    exists = db.execute(
        select(BankAccount).where(
            BankAccount.bank_code == payload.bank_code,
            BankAccount.account_no == payload.account_no,
        )
    ).scalars().first()
    if exists:
        raise HTTPException(status_code=400, detail="该银行账户已存在")
    account = BankAccount(
        enterprise_id=payload.enterprise_id or enterprise.id,
        bank_code=payload.bank_code,
        account_no=payload.account_no,
        account_name=payload.account_name,
        ukey_device_id=payload.ukey_device_id,
        collect_enabled=payload.collect_enabled,
        collect_cron=payload.collect_cron,
        days_back=payload.days_back,
    )
    db.add(account)
    db.flush()
    db.add(
        Credential(
            bank_account_id=account.id,
            username_enc=security.encrypt(payload.username),
            password_enc=security.encrypt(payload.password),
            pin_enc=security.encrypt(payload.pin),
        )
    )
    db.commit()
    return {"id": account.id}


@router.post("/accounts/{account_id}/credential")
def update_credential(account_id: int, payload: CredentialUpdate, db: Session = Depends(get_db)):
    account = db.get(BankAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账户不存在")
    credential = db.execute(select(Credential).where(Credential.bank_account_id == account_id)).scalars().first()
    if credential is None:
        credential = Credential(bank_account_id=account_id)
        db.add(credential)
    credential.username_enc = security.encrypt(payload.username)
    credential.password_enc = security.encrypt(payload.password)
    credential.pin_enc = security.encrypt(payload.pin)
    credential.updated_at = dt.datetime.now()
    db.commit()
    return {"ok": True}


@router.post("/accounts/{account_id}/collect")
def collect_now(account_id: int, db: Session = Depends(get_db)):
    account = db.get(BankAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账户不存在")
    start, end = task_range(account)
    tasks = [
        enqueue_task(db, account, task_type, start, end, trigger="manual", created_by="ui")
        for task_type in ("statements", "receipts")
    ]
    return {"tasks": [{"id": t.id, "type": t.type, "status": t.status} for t in tasks]}


@router.post("/accounts/{account_id}/toggle")
def toggle_account(account_id: int, db: Session = Depends(get_db)):
    try:
        enabled = account_admin.toggle_collection(db, account_id)
    except account_admin.AccountAdminError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return {"ok": True, "collect_enabled": enabled}


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, confirm: str = "", db: Session = Depends(get_db)):
    try:
        return account_admin.delete_account(db, account_id, confirm)
    except account_admin.AccountAdminError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/devices")
def list_devices(db: Session = Depends(get_db)):
    devices = db.execute(
        select(UkeyDevice).order_by(UkeyDevice.backend, UkeyDevice.gf3_cabinet, UkeyDevice.gf3_port)
    ).scalars().all()
    agents = {agent.code: agent.name for agent in db.execute(select(Agent)).scalars().all()}
    return {
        "items": [
            {
                "id": d.id,
                "name": d.name,
                "bank_code": d.bank_code,
                "backend": d.backend,
                "agent_code": d.agent_code,
                "agent_name": agents.get(d.agent_code, d.agent_code),
                "external_id": d.external_id,
                "gf3": f"{d.gf3_cabinet}/{d.gf3_port}",
                "cabinet": d.gf3_cabinet,
                "port": d.gf3_port,
                "ter_id": (d.extra or {}).get("ter_id", ""),
                "pid": d.external_id,
                "status": d.status,
                "cert_expires_at": d.cert_expires_at,
                "last_heartbeat": d.last_heartbeat,
            }
            for d in devices
        ]
    }


@router.get("/agents")
def list_agents(db: Session = Depends(get_db)):
    agents = db.execute(select(Agent).order_by(Agent.code)).scalars().all()
    return {
        "items": [
            {
                "id": agent.id,
                "code": agent.code,
                "name": agent.name,
                "hostname": agent.hostname,
                "ip": agent.ip,
                "status": agent.status,
                "last_heartbeat": agent.last_heartbeat,
                "capabilities": agent.capabilities,
            }
            for agent in agents
        ]
    }


@router.post("/devices")
def create_device(payload: DeviceCreate, db: Session = Depends(get_db)):
    extra = {}
    if payload.ter_id:
        extra["ter_id"] = payload.ter_id.strip()
    if payload.mb_user:
        extra["mb_user"] = payload.mb_user.strip()
    device = UkeyDevice(
        **payload.model_dump(exclude={"ter_id", "mb_user", "cert_expires_at"}),
        cert_expires_at=payload.cert_expires_at,
        extra=extra,
    )
    db.add(device)
    db.commit()
    return {"id": device.id}


@router.post("/devices/sync")
def sync_robot_devices(source: str = "auto", db: Session = Depends(get_db)):
    try:
        return device_sync.sync_devices(db, source=source)
    except device_sync.DeviceSyncError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except UsbRobotError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"USB 机器人接口请求失败: {exc}")
