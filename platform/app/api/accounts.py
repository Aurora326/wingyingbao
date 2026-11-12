from __future__ import annotations

import datetime as dt
import hashlib
import secrets
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Agent, AuditLog, BankAccount, BalanceSnapshot, CollectionTask, Credential, Enterprise, UkeyDevice
from app.schemas import AccountCreate, AccountDeviceUpdate, AccountUpdate, AgentCreate, CredentialUpdate, DeviceCreate
from app.services import account_admin, device_sync, permissions, security
from app.services.agents import resolve_agent_code
from app.services.scheduler import enqueue_task, task_range, task_types_for
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
                "collect_mode": account.collect_mode or "both",
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
        collect_mode=payload.collect_mode,
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


def _apply_device_binding(db: Session, account: BankAccount, device_id: int | None) -> dict:
    active = db.execute(
        select(CollectionTask).where(
            CollectionTask.account_id == account.id,
            CollectionTask.status.in_(("leased", "running")),
        )
    ).scalars().first()
    if active is not None:
        raise HTTPException(status_code=409, detail="该账户有正在执行的任务，请结束后再调整 UKey")

    device = None
    if device_id is not None:
        device = db.get(UkeyDevice, device_id)
        if device is None:
            raise HTTPException(status_code=404, detail="UKey 设备不存在")
        if device.bank_code and account.bank_code and device.bank_code != account.bank_code:
            raise HTTPException(status_code=400, detail="UKey 所属银行与账户不一致")
        other = db.execute(
            select(BankAccount).where(
                BankAccount.ukey_device_id == device.id,
                BankAccount.id != account.id,
            )
        ).scalars().first()
        if other is not None:
            tail = other.account_no[-4:] if other.account_no else other.id
            raise HTTPException(status_code=400, detail=f"该 UKey 已绑定其他账户（尾号 {tail}）")

    old_device_id = account.ukey_device_id
    if old_device_id == (device.id if device else None):
        return {
            "device_id": old_device_id,
            "device": device.name if device else "",
            "agent_code": resolve_agent_code(db, account),
            "queued_tasks": 0,
            "changed": False,
        }

    account.ukey_device_id = device.id if device else None
    agent_code = resolve_agent_code(db, account)
    queued = db.execute(
        select(CollectionTask).where(
            CollectionTask.account_id == account.id,
            CollectionTask.status == "queued",
        )
    ).scalars().all()
    for task in queued:
        task.device_id = device.id if device else None
        task.agent_code = agent_code

    db.add(
        AuditLog(
            actor="ui",
            action="account_device_update",
            target_type="bank_account",
            target_id=str(account.id),
            detail={
                "old_device_id": old_device_id,
                "new_device_id": device.id if device else None,
                "agent_code": agent_code,
                "queued_tasks": len(queued),
            },
        )
    )
    return {
        "device_id": device.id if device else None,
        "device": device.name if device else "",
        "agent_code": agent_code,
        "queued_tasks": len(queued),
        "changed": True,
    }


@router.post("/accounts/{account_id}/device")
def update_account_device(
    account_id: int, payload: AccountDeviceUpdate, db: Session = Depends(get_db)
):
    account = db.get(BankAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账户不存在")
    result = _apply_device_binding(db, account, payload.ukey_device_id)
    db.commit()
    return {"ok": True, **result}


@router.patch("/accounts/{account_id}")
def update_account(
    account_id: int, payload: AccountUpdate, db: Session = Depends(get_db)
):
    account = db.get(BankAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账户不存在")

    changed: list[str] = []
    if payload.account_name is not None:
        account.account_name = payload.account_name
        changed.append("account_name")
    if payload.collect_cron is not None:
        account.collect_cron = payload.collect_cron.strip()
        changed.append("collect_cron")
    if payload.collect_mode is not None:
        account.collect_mode = payload.collect_mode
        changed.append("collect_mode")
    if payload.days_back is not None:
        account.days_back = payload.days_back
        changed.append("days_back")

    binding = None
    if "ukey_device_id" in payload.model_fields_set:
        binding = _apply_device_binding(db, account, payload.ukey_device_id)
        if binding.get("changed"):
            changed.append("ukey_device_id")

    credential_fields = {
        key: getattr(payload, key)
        for key in ("username", "password", "pin")
        if getattr(payload, key) is not None
    }
    if credential_fields:
        credential = db.execute(
            select(Credential).where(Credential.bank_account_id == account_id)
        ).scalars().first()
        if credential is None:
            credential = Credential(bank_account_id=account_id)
            db.add(credential)
        for key, value in credential_fields.items():
            setattr(credential, f"{key}_enc", security.encrypt(value))
        credential.updated_at = dt.datetime.now()
        changed.append("credential")

    if changed:
        db.add(
            AuditLog(
                actor="ui",
                action="account_update",
                target_type="bank_account",
                target_id=str(account_id),
                detail={"fields": changed},
            )
        )
    db.commit()
    return {
        "ok": True,
        "changed": changed,
        "device_id": binding["device_id"] if binding else account.ukey_device_id,
        "queued_tasks": binding["queued_tasks"] if binding else 0,
    }


@router.post("/accounts/{account_id}/collect")
def collect_now(
    account_id: int,
    task_type: Annotated[
        str | None, Query(alias="type", pattern="^(statements|receipts|both)$")
    ] = None,
    db: Session = Depends(get_db),
):
    account = db.get(BankAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账户不存在")
    mode = task_type or account.collect_mode or "both"
    start, end = task_range(account)
    tasks = [
        enqueue_task(db, account, kind, start, end, trigger="manual", created_by="ui")
        for kind in task_types_for(mode)
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


@router.post("/agents")
def create_agent(payload: AgentCreate, request: Request, db: Session = Depends(get_db)):
    """管理员新增执行机：生成独立令牌，采集机首次心跳后自动上线。"""
    user = getattr(request.state, "user", None)
    if user is None or permissions.effective_role(user.role) != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以新增执行机")
    code = payload.code.strip()
    name = payload.name.strip()
    if db.execute(select(Agent).where(Agent.code == code)).scalars().first():
        raise HTTPException(status_code=409, detail="Agent 编号已存在")
    token = secrets.token_urlsafe(32)
    agent = Agent(
        code=code,
        name=name,
        status="offline",
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        token_enc=security.encrypt(token),
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return {"id": agent.id, "code": agent.code, "name": agent.name, "token": token}


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
                # 心跳超过窗口就按离线展示，避免进程退出后记录一直显示在线
                "status": (
                    "offline"
                    if agent.last_heartbeat
                    and dt.datetime.now() - agent.last_heartbeat > dt.timedelta(minutes=10)
                    else (agent.status or "offline")
                ),
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
