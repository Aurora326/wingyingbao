from __future__ import annotations

import datetime as dt
import socket

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Agent, BankAccount, UkeyDevice


def _detect_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        finally:
            sock.close()
    except OSError:
        return "127.0.0.1"


def ensure_local_agent(db: Session) -> Agent:
    agent = db.execute(select(Agent).where(Agent.code == settings.agent_code)).scalars().first()
    hostname = socket.gethostname()
    ip = _detect_ip()
    if agent is None:
        agent = Agent(
            code=settings.agent_code,
            name=settings.agent_name or hostname,
            hostname=hostname,
            ip=ip,
            status="online",
            last_heartbeat=dt.datetime.now(),
            capabilities={"backends": ["gf3", "mb"], "desktop": True},
        )
        db.add(agent)
        db.flush()
        return agent
    agent.hostname = hostname
    agent.ip = ip or agent.ip
    if settings.agent_name:
        agent.name = settings.agent_name
    agent.status = "online"
    agent.last_heartbeat = dt.datetime.now()
    return agent


def heartbeat(db: Session, code: str | None = None, status: str = "online") -> None:
    agent = db.execute(select(Agent).where(Agent.code == (code or settings.agent_code))).scalars().first()
    if agent is None:
        return
    agent.status = status
    agent.last_heartbeat = dt.datetime.now()
    db.commit()


def resolve_agent_code(db: Session, account: BankAccount) -> str:
    if account.ukey_device_id:
        device = db.get(UkeyDevice, account.ukey_device_id)
        if device is not None and device.agent_code:
            return device.agent_code
    return settings.agent_code
