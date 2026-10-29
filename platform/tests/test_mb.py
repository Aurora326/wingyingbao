from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from app.models import BankAccount, Enterprise, UkeyDevice
from app.services.executor import _build_task_payload
from app.services.scheduler import enqueue_task
from common.collect.mb import MbController, MbError


class FakeProc:
    def __init__(self, returncode: int = 0, stdout: str = '{"success": true}', stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _exe(tmp_path):
    path = tmp_path / "mb.exe"
    path.write_bytes(b"")
    return path


def test_mb_commands_build_expected_arguments(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return FakeProc()

    monkeypatch.setattr("common.cabinet_controller.subprocess.run", fake_run)
    exe = _exe(tmp_path)
    controller = MbController(exe=str(exe), ter_id="068330001533", port=24, user="15711668178")

    assert controller.connect()
    assert calls[-1] == [
        str(exe),
        "load_port",
        "--ter_id",
        "068330001533",
        "--port",
        "24",
        "--user",
        "15711668178",
    ]

    assert controller.press()
    assert calls[-1] == [str(exe), "robot", "--ter_id", "068330001533", "--port", "24"]

    assert controller.disconnect()
    assert calls[-1] == [
        str(exe),
        "shutdown_port",
        "--ter_id",
        "068330001533",
        "--port",
        "24",
        "--user",
        "15711668178",
    ]


def test_mb_command_failure_raises(tmp_path, monkeypatch):
    def fake_run(cmd, **kwargs):
        return FakeProc(returncode=1, stdout="", stderr="port busy")

    monkeypatch.setattr("common.cabinet_controller.subprocess.run", fake_run)
    controller = MbController(exe=str(_exe(tmp_path)), ter_id="068330001533", port=1, user="u")
    with pytest.raises(MbError, match="port busy"):
        controller.connect()


def test_mb_result_false_raises(tmp_path, monkeypatch):
    def fake_run(cmd, **kwargs):
        return FakeProc(stdout='{"result": false, "error": "errcode -1005"}')

    monkeypatch.setattr("common.cabinet_controller.subprocess.run", fake_run)
    controller = MbController(exe=str(_exe(tmp_path)), ter_id="068330001533", port=1, user="u")
    with pytest.raises(MbError, match="-1005"):
        controller.connect()


def test_payload_carries_mb_identity(db):
    enterprise = db.execute(select(Enterprise).order_by(Enterprise.id)).scalars().first()
    if enterprise is None:
        enterprise = Enterprise(name="MB 测试企业")
        db.add(enterprise)
        db.flush()
    device = UkeyDevice(
        name="MB-1533-24",
        bank_code="citic",
        backend="mb",
        gf3_cabinet=1533,
        gf3_port=24,
        extra={"ter_id": "068330001533", "mb_user": "15711668178"},
    )
    db.add(device)
    db.flush()
    account = BankAccount(
        enterprise_id=enterprise.id,
        bank_code="citic",
        account_no="MBPAYLOAD001",
        account_name="MB 载荷测试",
        ukey_device_id=device.id,
    )
    db.add(account)
    db.commit()

    task = enqueue_task(
        db, account, "statements", dt.date(2026, 3, 1), dt.date(2026, 3, 31), trigger="manual", force=True
    )
    payload = _build_task_payload(db, task)
    assert payload["device_backend"] == "mb"
    assert payload["ter_id"] == "068330001533"
    assert payload["mb_user"] == "15711668178"
    assert payload["gf3_port"] == 24


def test_executor_releases_mb_device(monkeypatch):
    import app.services.device.mb as mb_module
    from app.services import executor

    calls: list[tuple[str, dict]] = []

    class FakeMb:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        def disconnect(self) -> bool:
            calls.append(("disconnect", {}))
            return True

    monkeypatch.setattr(mb_module, "MbController", FakeMb)
    executor._release_device(
        {"device_backend": "mb", "gf3_port": 1, "ter_id": "068330001533", "mb_user": "u", "mb_exe": "mb.exe"}
    )
    assert [name for name, _ in calls] == ["init", "disconnect"]
    assert calls[0][1]["ter_id"] == "068330001533"
    assert calls[0][1]["port"] == 1


def test_unconfigured_bank_is_rejected():
    """没配采集程序的银行要明确报错并提示怎么加，而不是回退到某个默认程序。"""
    from app.services import collectors
    from app.services.banks import BANK_NAMES

    assert {"citic", "cib", "icbc", "nbcb", "njcb", "cgb", "ccb", "nb", "mock"} <= set(BANK_NAMES)

    with pytest.raises(collectors.CollectorNotConfigured):
        collectors.resolve("njcb", registry={})
