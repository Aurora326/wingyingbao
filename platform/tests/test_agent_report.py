from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import agent as agent_api
from app.main import app
from app.models import Agent, AuditLog, UkeyDevice
from app.schemas import AgentDeviceItem, AgentDeviceReport
from app.services.agent_report import apply_device_report


def _report(ter_id: str = "068330001517", agent_code: str = "cust-1517", ports=(1, 2)) -> AgentDeviceReport:
    return AgentDeviceReport(
        agent_code=agent_code,
        agent_name="客户机房采集盒子",
        hostname="CUST-PC",
        ip="192.168.63.20",
        version="device-report/1.0",
        ter_id=ter_id,
        ter_ip="192.168.63.252",
        port_count=30,
        devices=[
            AgentDeviceItem(
                port=port,
                bank_name="ICBC" if port == 1 else "CCB",
                status="busy" if port == 1 else "online",
                external_id=f"pid-{port}",
                usb_share=1 if port == 1 else 0,
                client_ip="192.168.63.20" if port == 1 else "",
            )
            for port in ports
        ],
    )


def test_agent_report_creates_agent_and_devices(db):
    result = apply_device_report(db, _report(ter_id="068330001517"))
    assert result["created"] == 2
    assert result["updated"] == 0
    assert result["missing"] == 0

    agent = db.execute(select(Agent).where(Agent.code == "cust-1517")).scalars().first()
    assert agent is not None
    assert agent.status == "online"
    assert agent.capabilities["terminals"][0]["ter_id"] == "068330001517"

    rows = db.execute(
        select(UkeyDevice).where(UkeyDevice.backend == "mb", UkeyDevice.gf3_cabinet == 1517)
    ).scalars().all()
    by_port = {row.gf3_port: row for row in rows}
    assert by_port[1].agent_code == "cust-1517"
    assert by_port[1].bank_code == "icbc"
    assert by_port[1].status == "busy"
    assert by_port[1].name == "工商银行 · 1号口"
    assert by_port[2].status == "online"
    assert by_port[2].name == "建设银行 · 2号口"

    log = db.execute(select(AuditLog).where(AuditLog.action == "agent_device_report")).scalars().first()
    assert log is not None
    assert log.detail["ter_id"] == "068330001517"


def test_agent_report_updates_and_marks_missing_offline(db):
    apply_device_report(db, _report(ter_id="068330001527", ports=(1, 2)))
    result = apply_device_report(db, _report(ter_id="068330001527", ports=(1,)))
    assert result["updated"] == 1
    assert result["missing"] == 1

    rows = db.execute(
        select(UkeyDevice).where(UkeyDevice.backend == "mb", UkeyDevice.gf3_cabinet == 1527)
    ).scalars().all()
    by_port = {row.gf3_port: row for row in rows}
    assert by_port[1].status == "busy"
    assert by_port[2].status == "offline"


def test_agent_report_preserves_existing_alias_and_certificate(db):
    existing = UkeyDevice(
        name="客户自己的别名",
        bank_code="icbc",
        backend="mb",
        agent_code="old-agent",
        gf3_cabinet=1537,
        gf3_port=1,
        cert_expires_at=dt.date(2027, 7, 30),
        extra={"ter_id": "068330001537"},
    )
    db.add(existing)
    db.commit()

    apply_device_report(db, _report(ter_id="068330001537", ports=(1,)))
    db.expire_all()
    row = db.execute(select(UkeyDevice).where(UkeyDevice.gf3_cabinet == 1537, UkeyDevice.gf3_port == 1)).scalars().first()
    assert row.name == "客户自己的别名"
    assert row.cert_expires_at == dt.date(2027, 7, 30)
    assert row.agent_code == "cust-1517"
    assert row.extra["source"] == "agent"


def test_agent_report_api_requires_token(monkeypatch):
    monkeypatch.setattr(agent_api, "settings", SimpleNamespace(agent_tokens={"test-token"}))
    payload = _report(ter_id="068330001519", agent_code="api-test").model_dump()
    with TestClient(app) as client:
        unauthorized = client.post("/api/agent/devices/report", json=payload)
        assert unauthorized.status_code == 401
        authorized = client.post(
            "/api/agent/devices/report",
            json=payload,
            headers={"Authorization": "Bearer test-token"},
        )
        assert authorized.status_code == 200
        assert authorized.json()["agent_code"] == "api-test"
