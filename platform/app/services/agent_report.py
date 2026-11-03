from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Agent, AuditLog, UkeyDevice
from app.schemas import AgentDeviceReport
from app.services import mb_local
from app.services.usb_robot import bank_code_from_name, cabinet_from_ter_id
from app.tenancy import current_tenant_id

MB_BACKEND = "mb"
DEVICE_STATUSES = {"online", "busy", "empty", "offline", "unknown"}


class AgentReportError(RuntimeError):
    pass


def _device_name(item, code: str) -> str:
    if item.port_name:
        return item.port_name[:64]
    label = mb_local.BANK_DISPLAY_NAMES.get(code) or item.bank_name or "UKey"
    return f"{label} · {item.port}号口"[:64]


def apply_device_report(db: Session, payload: AgentDeviceReport) -> dict:
    agent_code = (payload.agent_code or "").strip()
    ter_id = (payload.ter_id or "").strip()
    if not agent_code:
        raise AgentReportError("缺少 agent_code")
    if not ter_id:
        raise AgentReportError("缺少 ter_id")
    if len(agent_code) > 32:
        raise AgentReportError("agent_code 过长")

    now = dt.datetime.now()
    agent = db.execute(select(Agent).where(Agent.code == agent_code)).scalars().first()
    if agent is None:
        agent = Agent(code=agent_code, name=(payload.agent_name or payload.hostname or agent_code)[:64])
        db.add(agent)
        db.flush()
    agent.name = (payload.agent_name or agent.name or payload.hostname or agent_code)[:64]
    agent.hostname = (payload.hostname or agent.hostname or "")[:128]
    agent.ip = (payload.ip or agent.ip or "")[:64]
    agent.version = (payload.version or agent.version or "")[:32]
    agent.status = "online"
    agent.last_heartbeat = now
    capabilities = dict(agent.capabilities or {})
    terminals = [
        item
        for item in (capabilities.get("terminals") or [])
        if isinstance(item, dict) and str(item.get("ter_id")) != ter_id
    ]
    terminals.append(
        {
            "ter_id": ter_id,
            "ip": payload.ter_ip,
            "version": payload.version,
            "port_count": payload.port_count,
        }
    )
    capabilities.update({"backends": ["mb"], "terminals": terminals, "mode": "agent"})
    agent.capabilities = capabilities

    cabinet = cabinet_from_ter_id(ter_id)
    created = 0
    updated = 0
    seen: set[int] = set()
    for item in payload.devices:
        if item.port <= 0:
            continue
        seen.add(item.port)
        row = db.execute(
            select(UkeyDevice).where(
                UkeyDevice.backend == MB_BACKEND,
                UkeyDevice.gf3_cabinet == cabinet,
                UkeyDevice.gf3_port == item.port,
            )
        ).scalars().first()
        if row is None:
            row = UkeyDevice(
                name="",
                bank_code="",
                backend=MB_BACKEND,
                agent_code=agent_code,
                gf3_cabinet=cabinet,
                gf3_port=item.port,
            )
            db.add(row)
            created += 1
        else:
            updated += 1

        code = (item.bank_code or bank_code_from_name(item.bank_name or "")).strip().lower()
        if code and (code != "generic" or not row.bank_code):
            row.bank_code = code[:32]
        if not row.name:
            row.name = _device_name(item, code)
        row.agent_code = agent_code
        row.external_id = (item.external_id or row.external_id or "")[:64]
        row.status = item.status if item.status in DEVICE_STATUSES else "online"
        row.last_heartbeat = now
        extra = dict(row.extra or {})
        extra.update(
            {
                "ter_id": ter_id,
                "ter_ip": payload.ter_ip,
                "port_num": str(item.port),
                "pid": item.external_id,
                "port_name": item.port_name,
                "usb_share": str(item.usb_share),
                "client_ip": item.client_ip,
                "port_power": str(item.port_power),
                "user": item.user,
                "source": "agent",
                "agent_code": agent_code,
            }
        )
        row.extra = extra

    missing = 0
    rows = db.execute(
        select(UkeyDevice).where(
            UkeyDevice.backend == MB_BACKEND,
            UkeyDevice.gf3_cabinet == cabinet,
            UkeyDevice.agent_code == agent_code,
        )
    ).scalars().all()
    for row in rows:
        if row.gf3_port in seen:
            continue
        if (row.extra or {}).get("ter_id") not in (None, "", ter_id):
            continue
        row.status = "offline"
        extra = dict(row.extra or {})
        extra["missing_since"] = now.isoformat(timespec="seconds")
        row.extra = extra
        missing += 1

    db.add(
        AuditLog(
            actor=agent_code,
            action="agent_device_report",
            target_type="agent",
            target_id=agent_code,
            detail={
                "ter_id": ter_id,
                "total": len(payload.devices),
                "created": created,
                "updated": updated,
                "missing": missing,
            },
            tenant_id=agent.tenant_id or current_tenant_id(),
        )
    )
    db.flush()
    db.commit()
    return {
        "ok": True,
        "agent_code": agent_code,
        "ter_id": ter_id,
        "total": len(payload.devices),
        "created": created,
        "updated": updated,
        "missing": missing,
    }
