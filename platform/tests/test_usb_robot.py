from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.models import AuditLog, UkeyDevice
from app.services.device_sync import sync_devices
from app.services.usb_robot import (
    RobotDevice,
    UsbRobotClient,
    UsbRobotError,
    bank_code_from_name,
    cabinet_from_ter_id,
    parse_device,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "usb_robot_sample.json"


def _payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_payload_roundtrip():
    original = {"ticketed_point_no": "123", "user_no": "456", "listType": "0"}
    encoded = UsbRobotClient.encode_payload(original)
    assert UsbRobotClient.decode_payload(encoded) == original


def test_cabinet_from_ter_id():
    assert cabinet_from_ter_id("068330001533") == 1533
    assert cabinet_from_ter_id("") == 0


def test_bank_code_mapping():
    assert bank_code_from_name("中国工商银行(ICBC)") == "icbc"
    assert bank_code_from_name("中信银行(CNCB)") == "citic"
    assert bank_code_from_name("兴业银行(兴业网盾)") == "cib"
    assert bank_code_from_name("宁波银行(宁波银行)") == "nbcb"
    assert bank_code_from_name("南京银行") == "njcb"
    assert bank_code_from_name("广发银行") == "cgb"
    assert bank_code_from_name("民生银行(CMBC_UBAO)") == "cmbc"
    assert bank_code_from_name("上海农商银行(SHRCB_USBKEY)") == "shrcb"
    assert bank_code_from_name("BANKOFDALIAN") == "bod"
    assert bank_code_from_name("TLBANK") == "tlbank"
    assert bank_code_from_name("坚石e盾智能KEY") == "generic"


def test_parse_device():
    rows = _payload()["response"]["usbdata"]
    device = parse_device(rows[0])
    assert device.cabinet == 1533
    assert device.port == 1
    assert device.bank_code == "icbc"
    assert device.display_name == "测试企业A(制单)"
    assert device.status == "online"
    assert device.cert_expires_at == dt.date(2027, 7, 30)
    assert device.last_heartbeat == dt.datetime(2026, 5, 22, 15, 14, 5)
    assert device.cert_subject == "Composite Token Usbdisk"

    empty = parse_device(rows[2])
    assert empty.status == "empty"
    assert empty.cert_expires_at is None
    assert empty.bank_code == "citic"
    assert empty.display_name == "中信银行(CNCB)"


def test_client_posts_encoded_payload_and_parses():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = UsbRobotClient.decode_payload(request.content.decode())
        return httpx.Response(200, json=_payload())

    client = UsbRobotClient(
        base_url="https://example.test",
        token="token-abc",
        user_no="100008442",
        point_no="25073018072315410004",
        passwd="passwd-xyz",
        transport=httpx.MockTransport(handler),
    )
    devices = client.list_devices()
    assert len(devices) == 3
    assert seen["url"].endswith("/dev-api/userequ/new_get_usertick_usb_escalation")
    assert seen["headers"]["authorization"] == "Bearer token-abc"
    assert seen["headers"]["userno"] == "100008442"
    assert seen["body"]["ticketed_point_no"] == "25073018072315410004"
    assert seen["body"]["listType"] == "0"


def test_client_raises_on_business_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": "f", "response": {"errinfo": "缺少参数isUse"}})

    client = UsbRobotClient(base_url="https://example.test", token="t", user_no="u", point_no="p", transport=httpx.MockTransport(handler))
    with pytest.raises(UsbRobotError, match="缺少参数isUse"):
        client.list_devices()


def test_client_requires_configuration():
    client = UsbRobotClient(base_url="https://example.test", token="", user_no="", point_no="")
    assert not client.configured
    with pytest.raises(UsbRobotError):
        client.list_devices()


class FakeClient:
    def __init__(self, devices: list[RobotDevice]) -> None:
        self.devices = devices

    def list_devices(self) -> list[RobotDevice]:
        return list(self.devices)


def test_sync_devices_creates_then_updates(db):
    devices = [parse_device(row) for row in _payload()["response"]["usbdata"]]
    for device in devices:
        device.cabinet = 7777
    logs_before = len(db.execute(select(AuditLog).where(AuditLog.action == "usb_robot_sync")).scalars().all())
    first = sync_devices(db, FakeClient(devices))
    assert first == {"total": 3, "created": 3, "updated": 0, "source": "cloud"}

    rows = db.execute(select(UkeyDevice).where(UkeyDevice.gf3_cabinet == 7777)).scalars().all()
    assert len(rows) == 3
    port1 = next(row for row in rows if row.gf3_port == 1)
    assert port1.name == "测试企业A(制单)"
    assert port1.bank_code == "icbc"
    assert port1.backend == "mb"
    assert port1.external_id == "d001"
    assert port1.status == "online"
    assert port1.cert_expires_at == dt.date(2027, 7, 30)
    assert port1.extra["ter_id"] == "068330001533"
    assert port1.extra["pid"] == "d001"
    assert port1.extra["port_num"] == "1"

    second = sync_devices(db, FakeClient(devices))
    assert second == {"total": 3, "created": 0, "updated": 3, "source": "cloud"}
    assert len(db.execute(select(UkeyDevice).where(UkeyDevice.gf3_cabinet == 7777)).scalars().all()) == 3

    logs = db.execute(select(AuditLog).where(AuditLog.action == "usb_robot_sync")).scalars().all()
    assert len(logs) == logs_before + 2


def test_sync_keeps_gf3_device_separate(db):
    existing = UkeyDevice(
        name="gf3-9999-1",
        bank_code="citic",
        backend="gf3",
        gf3_cabinet=9999,
        gf3_port=1,
    )
    db.add(existing)
    db.commit()

    source = RobotDevice(
        cabinet=9999,
        port=1,
        bank_name="中信银行(CNCB)",
        bank_code="citic",
        org_name="MB-9999-1",
        status="online",
        cert_subject="",
        cert_expires_at=None,
        last_heartbeat=None,
        external_id="pid-1",
    )
    result = sync_devices(db, FakeClient([source]))
    assert result == {"total": 1, "created": 1, "updated": 0, "source": "cloud"}

    db.expire_all()
    rows = db.execute(select(UkeyDevice).where(UkeyDevice.gf3_cabinet == 9999)).scalars().all()
    assert {row.backend for row in rows} == {"gf3", "mb"}
    assert db.get(UkeyDevice, existing.id).name == "gf3-9999-1"
    mb_row = next(row for row in rows if row.backend == "mb")
    assert mb_row.name == "MB-9999-1"
    assert mb_row.external_id == "pid-1"


def test_ensure_schema_adds_device_backend(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from app.db import ensure_schema

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE ukey_device (id INTEGER PRIMARY KEY, name VARCHAR(64), "
                "bank_code VARCHAR(32), gf3_cabinet INTEGER, gf3_port INTEGER, status VARCHAR(16), "
                "last_heartbeat DATETIME, cert_subject VARCHAR(256), cert_expires_at DATE)"
            )
        )
    ensure_schema(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("ukey_device")}
    assert {"backend", "external_id", "agent_code", "extra"} <= columns

    with engine.begin() as connection:
        connection.execute(text("INSERT INTO ukey_device (name, bank_code) VALUES ('旧设备', 'citic')"))
        assert connection.execute(text("SELECT backend FROM ukey_device")).scalar() == "gf3"
