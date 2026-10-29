from __future__ import annotations

import datetime as dt

from pathlib import Path

from sqlalchemy import select

from app.models import BankAccount, Enterprise, Statement
from app.services.ingest import ingest_statement, verify_balance_continuity
from app.services.parsers.statement import dedupe_hash, parse_statement
from bank_loader import bank_dir, load_driver_module, make_driver, profile_path

ICBC_DIR = Path(r"C:\wangyingbao\工商银行\icbc_automation\downloads\statements\2026\04")
ICBC_SAMPLES = {
    "工商银行_银行流水_20260415.xlsx": 34,
    "工商银行_银行流水_20260416.xlsx": 33,
    "工商银行_银行流水_20260420.xlsx": 40,
}


def test_icbc_samples_parse():
    for name, expected in ICBC_SAMPLES.items():
        path = ICBC_DIR / name
        assert path.exists(), f"缺少样本 {name}"
        parsed = parse_statement(path)
        assert len(parsed.rows) == expected, name
        assert parsed.account_no == "1001102009100108482"
        assert all(row.balance is not None for row in parsed.rows)

    parsed = parse_statement(ICBC_DIR / "工商银行_银行流水_20260415.xlsx")
    first = parsed.rows[0]
    assert first.txn_date.isoformat() == "2026-04-10"
    assert first.txn_time == "22:12:54"
    assert first.direction == "debit"
    assert str(first.amount) == "4.50"
    assert str(first.balance) == "253494.06"
    assert first.summary == "跨行汇款手续费"

    latest = parse_statement(ICBC_DIR / "工商银行_银行流水_20260420.xlsx")
    assert latest.rows[0].txn_date.isoformat() == "2026-04-16"
    assert str(latest.rows[0].balance) == "205448.91"


def test_icbc_dedupe_unique_within_file():
    parsed = parse_statement(ICBC_DIR / "工商银行_银行流水_20260415.xlsx")
    hashes = {dedupe_hash("icbc", parsed.account_no, row) for row in parsed.rows}
    assert len(hashes) == len(parsed.rows)


def _icbc_account(db) -> BankAccount:
    enterprise = db.execute(select(Enterprise).order_by(Enterprise.id)).scalars().first()
    if enterprise is None:
        enterprise = Enterprise(name="工行测试企业")
        db.add(enterprise)
        db.flush()
    account = BankAccount(
        enterprise_id=enterprise.id,
        bank_code="icbc",
        account_no="1001102009100108482",
        account_name="工行测试账户",
    )
    db.add(account)
    db.commit()
    return account


def test_icbc_ingest_idempotent_and_balance_continuous(db):
    account = _icbc_account(db)
    path = ICBC_DIR / "工商银行_银行流水_20260415.xlsx"

    first = ingest_statement(db, account, path, None)
    db.commit()
    assert first.inserted == 34
    assert first.failed == 0

    second = ingest_statement(db, account, path, None)
    assert second.inserted == 0
    assert second.duplicates == 34

    rows = db.execute(select(Statement).where(Statement.account_id == account.id)).scalars().all()
    assert len(rows) == 34
    continuous, breaks = verify_balance_continuity(db, account.id)
    assert continuous, breaks


def test_icbc_driver_registered():
    """每家银行一个独立项目：驱动、配置、入口都在自己的目录里。"""
    directory = bank_dir("icbc")
    for name in ("driver.py", "run_task.py", "profiles/icbc.yaml"):
        assert (directory / name).exists(), f"缺少 {name}"
    driver = make_driver("icbc")
    assert driver.bank_code == "icbc"
    assert driver.display_name == "工商银行"
    assert driver.sel("date_start_input")
    assert driver.url("login").startswith("https://corporbank-simp.icbc.com.cn")


def _run_context():
    import logging

    from common.collect.profile import BankProfile
    from common.collect.base import RunContext

    return RunContext(
        task={},
        profile=BankProfile.load("icbc", profile_path("icbc")),
        work_dir=Path("."),
        downloads_dir=Path("."),
        archive_root=Path("."),
        logger=logging.getLogger("test"),
    )


def test_icbc_certificate_popup_uses_ui_automation(monkeypatch):
    from common.collect.pin import PinInjector

    icbc_module = load_driver_module("icbc")

    calls: list[str] = []
    monkeypatch.setattr(icbc_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(icbc_module.windows_ui, "find_hwnd", lambda *a, **k: 123)
    monkeypatch.setattr(icbc_module.windows_ui, "double_click_in_dialog", lambda *a, **k: calls.append("double") or True)
    monkeypatch.setattr(icbc_module.windows_ui, "click_in_dialog", lambda *a, **k: calls.append("confirm") or False)
    monkeypatch.setattr(icbc_module.windows_ui, "click_chrome_dialog_button", lambda *a, **k: calls.append("chrome") or True)
    ctx = _run_context()
    ctx.pin = PinInjector("198310gao")
    monkeypatch.setattr(ctx.pin, "press", lambda *a, **k: calls.append("enter") or True)

    make_driver("icbc")._handle_certificate_popup(ctx)
    assert calls == ["double", "confirm"]


def test_icbc_certificate_popup_uses_chrome_uia(monkeypatch):
    from common.collect.pin import PinInjector

    icbc_module = load_driver_module("icbc")

    calls: list[str] = []
    monkeypatch.setattr(icbc_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(icbc_module.windows_ui, "find_hwnd", lambda *a, **k: None)
    monkeypatch.setattr(icbc_module.windows_ui, "click_chrome_dialog_button", lambda *a, **k: calls.append("chrome") or True)
    ctx = _run_context()
    ctx.pin = PinInjector("198310gao")
    monkeypatch.setattr(ctx.pin, "press", lambda *a, **k: calls.append("enter") or True)

    make_driver("icbc")._handle_certificate_popup(ctx)
    assert calls == ["chrome"]


def test_icbc_pin_popup_uses_aikeyboard(monkeypatch):
    from common.collect.pin import PinInjector

    icbc_module = load_driver_module("icbc")

    calls: list[str] = []
    monkeypatch.setattr(icbc_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(icbc_module.windows_ui, "click_in_dialog", lambda *a, **k: calls.append("field") or True)
    monkeypatch.setattr(icbc_module.windows_ui, "click_dialog_button", lambda *a, **k: calls.append("confirm") or True)
    monkeypatch.setattr(icbc_module.windows_ui, "dialog_visible", lambda *a, **k: False)
    ctx = _run_context()
    ctx.pin = PinInjector("198310gao")
    monkeypatch.setattr(ctx.pin, "aikeyboard", lambda: calls.append("aikeyboard") or True)
    monkeypatch.setattr(ctx.pin, "clear", lambda: calls.append("clear") or True)
    monkeypatch.setattr(ctx.pin, "pyautogui", lambda *a, **k: calls.append("pyautogui") or True)

    assert make_driver("icbc")._handle_pin_popup(ctx) is True
    assert calls == ["field", "clear", "aikeyboard", "confirm"]
    assert "pyautogui" not in calls


def _fast_idle(driver, seconds: float = 0.3):
    driver.profile.data.setdefault("options", {})["auth_idle_timeout"] = seconds


def test_icbc_auth_loop_handles_two_pin_prompts(monkeypatch):
    icbc_module = load_driver_module("icbc")

    driver = make_driver("icbc")
    pin_flags = [True, True, False]
    calls: list[str] = []

    def fake_popup_state():
        return (pin_flags.pop(0) if pin_flags else False), False

    monkeypatch.setattr(icbc_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(driver, "_popup_state", fake_popup_state)
    monkeypatch.setattr(icbc_module.windows_ui, "find_hwnd", lambda titles: None)
    monkeypatch.setattr(driver, "_logged_in", lambda page: True)
    monkeypatch.setattr(driver, "_handle_pin_popup", lambda ctx: calls.append("pin") or True)
    _fast_idle(driver)

    ok, handled = driver._handle_auth_loop(_run_context(), page=None, timeout=5)
    assert ok is True
    assert handled == 2
    assert calls == ["pin", "pin"]


def test_icbc_auth_loop_fails_when_pin_stays_open(monkeypatch):
    """密码错误弹窗不关闭时，即使处理过弹窗也不能判定登录成功。"""
    icbc_module = load_driver_module("icbc")

    driver = make_driver("icbc")
    monkeypatch.setattr(icbc_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(driver, "_popup_state", lambda: (True, False))
    monkeypatch.setattr(icbc_module.windows_ui, "find_hwnd", lambda titles: 123)
    monkeypatch.setattr(driver, "_logged_in", lambda page: True)
    monkeypatch.setattr(driver, "_handle_pin_popup", lambda ctx: False)
    _fast_idle(driver)

    ok, handled = driver._handle_auth_loop(_run_context(), page=None, timeout=2)
    assert ok is False
    assert handled == 1


def test_icbc_auth_loop_fails_when_page_not_logged_in(monkeypatch):
    """没有弹窗但页面也没进入登录态时，不能因为 handled>0 就当作已登录。"""
    icbc_module = load_driver_module("icbc")

    driver = make_driver("icbc")
    flags = [True, False]

    def fake_popup_state():
        return (flags.pop(0) if flags else False), False

    monkeypatch.setattr(icbc_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(driver, "_popup_state", fake_popup_state)
    monkeypatch.setattr(icbc_module.windows_ui, "find_hwnd", lambda titles: None)
    monkeypatch.setattr(driver, "_logged_in", lambda page: False)
    monkeypatch.setattr(driver, "_handle_pin_popup", lambda ctx: True)
    _fast_idle(driver)

    ok, handled = driver._handle_auth_loop(_run_context(), page=None, timeout=2)
    assert ok is False
    assert handled == 1


def test_windows_ui_title_matching():
    from common.collect import windows_ui

    assert windows_ui._title_matches("选择证书", ("选择证书",))
    assert windows_ui._title_matches("输入密码", ("校验U盾密码", "输入密码"))
    assert not windows_ui._title_matches("中国工商银行企业网上银行 - Google Chrome", ("输入密码",))
    assert not windows_ui._title_matches("", ("输入密码",))


def test_icbc_receipt_date_range_excludes_today():
    """工行强制要求：回单不能查/下载当天，结束日期最多到昨天。"""
    IcbcDriver = load_driver_module("icbc").IcbcDriver

    today = dt.date(2026, 10, 29)
    assert IcbcDriver.receipt_date_range("2026-10-01", "2026-10-29", today) == ("2026-10-01", "2026-10-28")
    assert IcbcDriver.receipt_date_range("2026-10-29", "2026-10-29", today) is None
    assert IcbcDriver.receipt_date_range("2026-09-01", "2026-10-20", today) == ("2026-09-01", "2026-10-20")


def _driver_without_profile():
    return load_driver_module("icbc").IcbcDriver.__new__(load_driver_module("icbc").IcbcDriver)


def _write_statement(path, with_data: bool) -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["[HISTORYDETAIL]"])
    sheet.append(["交易时间", "摘要", "转入金额", "转出金额", "余额", "对方单位"])
    if with_data:
        sheet.append(["2026-04-10 22:12:54", "跨行汇款手续费", "", "4.50", "253,494.06", ""])
    workbook.save(path)


def test_icbc_statement_without_rows_is_empty(tmp_path):
    """查询为空时工行照样下载一个只有标记行和表头的表，必须判成空，不能当有数据。"""
    driver = _driver_without_profile()
    empty = tmp_path / "empty.xlsx"
    _write_statement(empty, with_data=False)
    assert driver._statement_file_has_rows(empty) is False

    filled = tmp_path / "filled.xlsx"
    _write_statement(filled, with_data=True)
    assert driver._statement_file_has_rows(filled) is True


def test_icbc_unreadable_statement_file_is_treated_as_data(tmp_path):
    """读不出来的格式按有数据处理，避免把正常结果吞掉。"""
    driver = _driver_without_profile()
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"not a spreadsheet")
    assert driver._statement_file_has_rows(broken) is True


def test_icbc_empty_statement_without_suffix_is_detected(tmp_path):
    """浏览器下载下来的是无后缀的 GUID 文件名，按扩展名读会被拒，必须仍能判空。"""
    driver = _driver_without_profile()
    target = tmp_path / "ee95571e-0024-4f78-b5ea-043a4ab480da"
    _write_statement(target, with_data=False)
    assert driver._statement_file_has_rows(target) is False
