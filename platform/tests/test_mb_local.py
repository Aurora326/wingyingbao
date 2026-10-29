from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import AuditLog, UkeyDevice
from app.services import device_sync, mb_local
from app.services.device_sync import DeviceSyncError, sync_devices
from app.services.mb_local import MbLocalError, parse_terminal
from app.services.usb_robot import RobotDevice

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mb_status_sample.json"


def _payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _parsed():
    ter = _payload()["data"]["data"]["data"][0]
    return parse_terminal(ter)


def test_parse_local_terminal_and_ports():
    terminal, devices = _parsed()
    assert terminal.ter_id == "068330001533"
    assert terminal.ip == "192.168.254.101"
    assert terminal.mac == "48:AB:BD:DD:E5:BB"
    assert terminal.port_count == 30
    assert [device.port for device in devices] == [1, 2, 3, 4]

    by_port = {device.port: device for device in devices}
    assert by_port[1].bank_code == "icbc"
    assert by_port[1].status == "online"
    assert by_port[1].external_id == "d001"
    assert by_port[1].raw["source"] == "local"
    assert by_port[2].status == "busy"
    assert by_port[2].bank_code == "ccb"
    assert by_port[3].status == "empty"
    assert by_port[4].bank_code == "bod"
    assert by_port[4].raw["port_name"] == "大连银行(复核)"
    assert mb_local.local_device_name(by_port[1]) == "工商银行 · 1号口"
    assert mb_local.local_device_name(by_port[4]) == "大连银行(复核)"


def test_read_terminal_accepts_nested_mb_status(monkeypatch):
    payload = _payload()

    class FakeController:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def status(self):
            return payload

    monkeypatch.setattr(mb_local, "MbController", FakeController)
    terminal, devices = mb_local.read_terminal("068330001533")
    assert terminal.ter_id == "068330001533"
    assert len(devices) == 4


def test_local_sync_preserves_alias_and_certificate(db, monkeypatch):
    existing = UkeyDevice(
        name="客户A(制单)",
        bank_code="icbc",
        backend="mb",
        agent_code="win-01",
        gf3_cabinet=1533,
        gf3_port=1,
        cert_expires_at=dt.date(2027, 7, 30),
        extra={"ter_id": "068330001533", "mb_user": "u"},
    )
    db.add(existing)
    db.commit()

    terminal, devices = _parsed()
    monkeypatch.setattr(device_sync.mb_local, "read_all", lambda *args, **kwargs: ([terminal], devices, []))
    result = sync_devices(db, source="local")
    assert result == {
        "total": 4,
        "created": 3,
        "updated": 1,
        "source": "local",
        "terminals": 1,
        "errors": [],
    }

    db.expire_all()
    rows = db.execute(select(UkeyDevice).where(UkeyDevice.backend == "mb", UkeyDevice.gf3_cabinet == 1533)).scalars().all()
    by_port = {row.gf3_port: row for row in rows}
    assert by_port[1].name == "客户A(制单)"
    assert by_port[1].cert_expires_at == dt.date(2027, 7, 30)
    assert by_port[1].agent_code == "win-01"
    assert by_port[1].extra["source"] == "local"
    assert by_port[2].name == "建设银行 · 2号口"
    assert by_port[2].status == "busy"
    assert by_port[3].status == "empty"

    log = db.execute(select(AuditLog).where(AuditLog.action == "usb_robot_sync").order_by(AuditLog.id.desc())).scalars().first()
    assert log is not None
    assert log.detail["source"] == "local"
    assert log.detail["terminals"] == ["068330001533"]


def test_auto_falls_back_to_cloud_when_local_fails(db, monkeypatch):
    def fail(*args, **kwargs):
        raise MbLocalError("mb.exe unavailable")

    monkeypatch.setattr(device_sync.mb_local, "read_all", fail)
    monkeypatch.setattr(
        device_sync,
        "settings",
        SimpleNamespace(
            usb_robot_enabled=True,
            mb_ter_id_list=["068330001533"],
            mb_exe="",
            mb_user="",
            agent_code="local",
        ),
    )
    source = RobotDevice(
        cabinet=9991,
        port=5,
        bank_name="中信银行(CNCB)",
        bank_code="citic",
        org_name="回退设备",
        status="online",
        cert_subject="",
        cert_expires_at=None,
        last_heartbeat=None,
        external_id="pid-5",
    )
    monkeypatch.setattr(
        device_sync.UsbRobotClient,
        "from_settings",
        classmethod(lambda cls: SimpleNamespace(list_devices=lambda: [source])),
    )
    result = sync_devices(db, source="auto")
    assert result["source"] == "cloud"
    assert result["created"] == 1


def test_local_failure_without_cloud_raises(db, monkeypatch):
    def fail(*args, **kwargs):
        raise MbLocalError("mb.exe unavailable")

    monkeypatch.setattr(device_sync.mb_local, "read_all", fail)
    monkeypatch.setattr(
        device_sync,
        "settings",
        SimpleNamespace(
            usb_robot_enabled=False,
            mb_ter_id_list=["068330001533"],
            mb_exe="",
            mb_user="",
            agent_code="local",
        ),
    )
    with pytest.raises(DeviceSyncError, match="未配置服务商接口"):
        sync_devices(db, source="auto")
    with pytest.raises(DeviceSyncError, match="本地 MB 同步失败"):
        sync_devices(db, source="local")
