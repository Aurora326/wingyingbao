from __future__ import annotations

import base64
import io
import json
import re
import time
import zipfile
from pathlib import Path

import pytest

from common.collect import flow
from common.collect.archive import archive_file
from common.collect.browser import BrowserSession, extract_crx, extension_id, extension_installed
from common.collect.download import HTML_SUFFIXES, DownloadWatcher, is_candidate, is_html, newest_since
from common.collect.pin import PinInjector
from common.collect.profile import BankProfile
from bank_loader import bank_dir, load_driver_module, profile_path


class FakeLocator:
    def __init__(self, visible: bool) -> None:
        self.visible = visible
        self.actions: list[str] = []

    @property
    def first(self) -> "FakeLocator":
        return self

    def count(self) -> int:
        return 1

    def is_visible(self, timeout: int = 0) -> bool:
        return self.visible

    def wait_for(self, state: str = "attached", timeout: int = 0) -> None:
        return None

    def click(self, timeout: int = 0, force: bool = False) -> None:
        self.actions.append("click")

    def evaluate(self, script: str, arg=None):
        self.actions.append("evaluate")
        return True

    def fill(self, value: str) -> None:
        self.actions.append(f"fill:{value}")

    def input_value(self, timeout: int = 0) -> str:
        return ""


class FakeScope:
    def __init__(self, visible_markers: set[str] | None = None, evaluate_result: bool = True) -> None:
        self.visible_markers = visible_markers or set()
        self.evaluate_result = evaluate_result
        self.evaluate_calls: list[tuple[str, object]] = []

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(any(marker in selector for marker in self.visible_markers))

    def evaluate(self, script: str, arg=None):
        self.evaluate_calls.append((script, arg))
        return self.evaluate_result

    def wait_for_timeout(self, ms: int) -> None:
        return None


class StubDriver:
    def __init__(self, selectors: dict[str, list[str]], options: dict | None = None) -> None:
        self._selectors = selectors
        self._options = options or {}

    def sel(self, key: str) -> list[str]:
        return self._selectors.get(key, [])

    def option(self, key: str, default=None):
        return self._options.get(key, default)


def _make_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", "{}")
        archive.writestr("hello.txt", "ok")
    return buffer.getvalue()


def test_extract_crx_v3_and_v2(tmp_path):
    payload = _make_zip()
    header = json.dumps({"crx_version": "3"}).encode("utf-8")

    v3 = tmp_path / "v3.crx"
    v3.write_bytes(b"Cr24" + (3).to_bytes(4, "little") + len(header).to_bytes(4, "little") + header + payload)
    extracted = extract_crx(str(v3), tmp_path / "out")
    assert extracted is not None
    assert (extracted / "hello.txt").read_text(encoding="utf-8") == "ok"

    pubkey = b"k" * 8
    signature = b"s" * 6
    v2 = tmp_path / "v2.crx"
    v2.write_bytes(
        b"Cr24"
        + (2).to_bytes(4, "little")
        + len(pubkey).to_bytes(4, "little")
        + len(signature).to_bytes(4, "little")
        + pubkey
        + signature
        + payload
    )
    extracted2 = extract_crx(str(v2), tmp_path / "out2")
    assert extracted2 is not None
    assert (extracted2 / "hello.txt").exists()


def test_extension_id_and_installed(tmp_path):
    ext_dir = tmp_path / "ext"
    metadata_dir = ext_dir / "_metadata"
    metadata_dir.mkdir(parents=True)
    payload = base64.b64encode(json.dumps({"item_id": "abcdefghijklmnop"}).encode()).decode()
    (metadata_dir / "verified_contents.json").write_text(json.dumps({"payload": payload}), encoding="utf-8")
    assert extension_id(ext_dir) == "abcdefghijklmnop"

    profile = tmp_path / "profile"
    assert not extension_installed(profile, ext_dir)
    (profile / "Default" / "Extensions" / "abcdefghijklmnop").mkdir(parents=True)
    assert extension_installed(profile, ext_dir)


def test_browser_ignores_disable_extensions_flag(tmp_path):
    session = BrowserSession(user_data_dir=tmp_path / "profile", downloads_dir=tmp_path / "dl")
    args, ignore = session._extension_args()
    assert args == []
    assert "--disable-extensions" in ignore

    payload = _make_zip()
    header = json.dumps({"crx_version": "3"}).encode("utf-8")
    crx = tmp_path / "cib.crx"
    crx.write_bytes(b"Cr24" + (3).to_bytes(4, "little") + len(header).to_bytes(4, "little") + header + payload)
    with_ext = BrowserSession(user_data_dir=tmp_path / "profile2", downloads_dir=tmp_path / "dl2", extension_path=str(crx))
    args, ignore = with_ext._extension_args()
    assert any(arg.startswith("--load-extension=") for arg in args)
    assert "--disable-extensions" in ignore


def test_download_watcher_accepts_guid_and_skips_partial(tmp_path):
    guid = tmp_path / "3f2a8f9e-1c2b-4d3e-9f00-aa11bb22cc33"
    guid.write_bytes(b"x" * 400)
    partial = tmp_path / "report.xlsx.crdownload"
    partial.write_bytes(b"x" * 5000)
    html = tmp_path / "download.html"
    html.write_text("<html>" + "x" * 500 + "</html>", encoding="utf-8")

    assert is_candidate(guid, guid.stat().st_size)
    assert not is_candidate(partial, partial.stat().st_size)
    assert is_candidate(html, html.stat().st_size)
    assert is_html(html) and not is_html(guid)

    watcher = DownloadWatcher([tmp_path], timeout=5, settle_checks=2, interval=0.1)
    found = watcher.wait({})
    assert found is not None
    assert found.suffix.lower() in HTML_SUFFIXES or found.name == guid.name

    newer = newest_since([tmp_path], time.time() - 60)
    assert newer is not None


def test_set_date_range_writes_into_given_scope():
    scope = FakeScope(evaluate_result=True)
    driver = StubDriver(
        {
            "date_start_input": ["input#startDate"],
            "date_end_input": ["input#endDate"],
        }
    )
    assert flow.set_date_range(scope, driver, "2026-02-01", "2026-02-28")
    payloads = [call[1] for call in scope.evaluate_calls]
    assert payloads == [[["input#startDate"], "2026-02-01"], [["input#endDate"], "2026-02-28"]]
    assert all("document.querySelector" in call[0] for call in scope.evaluate_calls)


def test_set_date_range_clicks_other_when_configured():
    scope = FakeScope(visible_markers={"其他"}, evaluate_result=True)
    driver = StubDriver(
        {
            "date_other": ["text=其他"],
            "date_start_input": ["input[name='startDate']"],
            "date_end_input": ["input[name='endDate']"],
        },
        {"date_click_other": True},
    )
    assert flow.set_date_range(scope, driver, "2026-01-01", "2026-01-31")
    assert len(scope.evaluate_calls) == 2


def test_has_data_only_depends_on_no_data_marker():
    driver = StubDriver({"no_data": ["text=无数据显示", "#nothing"]})
    assert flow.has_data(FakeScope(), driver) is True
    assert flow.has_data(FakeScope(visible_markers={"无数据显示"}), driver) is False


def test_archive_keeps_accounts_apart(tmp_path):
    root = tmp_path / "statements"
    first = tmp_path / "a.xlsx"
    second = tmp_path / "b.xlsx"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    p1 = archive_file(first, root, "中信银行_银行流水", account_suffix="6222")
    p2 = archive_file(second, root, "中信银行_银行流水", account_suffix="8110")
    assert p1.exists() and p2.exists()
    assert p1 != p2


def test_pin_injector_prefers_aikeyboard(tmp_path, monkeypatch):
    injector = PinInjector("123456", str(tmp_path / "AiKeyboard.exe"))
    calls: list[str] = []

    monkeypatch.setattr(injector, "aikeyboard", lambda: calls.append("aikeyboard") or True)
    monkeypatch.setattr(injector, "fill", lambda scope, selectors: calls.append("fill") or True)
    monkeypatch.setattr(injector, "js_set", lambda scope, selectors: calls.append("js") or True)
    monkeypatch.setattr(injector, "pyautogui", lambda click_point=None: calls.append("pyautogui") or True)

    assert injector.inject(FakeScope(), ["#passwordId"])
    assert calls == ["aikeyboard"]


def test_prepare_only_touches_gf3_backend(monkeypatch):
    import logging

    import common.collect.base as base
    from common.collect.base import RunContext
    MockDriver = load_driver_module("mock").MockDriver

    calls: list[tuple[str, int]] = []

    class FakeGf3:
        def __init__(self, exe: str = "gf3", cabinet: int = 1) -> None:
            self.cabinet = cabinet

        def isolate(self, port: int) -> None:
            calls.append(("isolate", port))

        def disconnect(self, port: int) -> None:
            calls.append(("disconnect", port))

    class FakeMb:
        def __init__(self, exe: str = "", ter_id: str = "", port: int = 1, user: str = "") -> None:
            self.ter_id = ter_id
            self.port = port
            self.user = user

        def connect(self) -> bool:
            calls.append(("mb_load", self.port))
            return True

        def wait_ready(self, seconds: int = 0) -> None:
            return None

        def disconnect(self) -> bool:
            calls.append(("mb_unload", self.port))
            return True

    monkeypatch.setattr(base, "Gf3Controller", FakeGf3)
    monkeypatch.setattr(base, "MbController", FakeMb)
    driver = MockDriver(BankProfile({}))

    def make_ctx(backend: str) -> RunContext:
        return RunContext(
            task={
                "gf3_cabinet": 1533,
                "gf3_port": 3,
                "device_backend": backend,
                "ter_id": "068330001533",
            },
            profile=BankProfile({}),
            work_dir=Path("."),
            downloads_dir=Path("."),
            archive_root=Path("."),
            logger=logging.getLogger("test"),
        )

    driver.prepare(make_ctx("mb"))
    assert calls == [("mb_load", 3)]

    calls.clear()
    driver.prepare(make_ctx("gf3"))
    assert calls == [("isolate", 3)]


def test_prepare_rejects_mb_without_ter_id(monkeypatch):
    import logging

    import common.collect.base as base
    from common.collect.base import RunContext
    MockDriver = load_driver_module("mock").MockDriver

    monkeypatch.setattr(base, "MbController", lambda **kwargs: None)
    ctx = RunContext(
        task={"gf3_port": 3, "device_backend": "mb"},
        profile=BankProfile({}),
        work_dir=Path("."),
        downloads_dir=Path("."),
        archive_root=Path("."),
        logger=logging.getLogger("test"),
    )
    with pytest.raises(RuntimeError, match="ter_id"):
        MockDriver(BankProfile({})).prepare(ctx)


def test_browser_accepts_extension_directory(tmp_path):
    extension = tmp_path / "ext"
    extension.mkdir()
    (extension / "manifest.json").write_text('{"name": "test"}', encoding="utf-8")
    session = BrowserSession(
        user_data_dir=tmp_path / "profile",
        downloads_dir=tmp_path / "dl",
        extension_path=str(extension),
    )
    args, ignore = session._extension_args()
    assert any(str(extension) in arg for arg in args)
    assert "--disable-extensions" in ignore


def test_pin_press_uses_aikeyboard(monkeypatch, tmp_path):
    from common.collect.pin import PinInjector

    exe = tmp_path / "AiKeyboard.exe"
    exe.write_bytes(b"")
    calls: list[list[str]] = []

    class FakeProc:
        returncode = 0

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return FakeProc()

    monkeypatch.setattr("common.collect.pin.subprocess.run", fake_run)
    monkeypatch.setattr("common.collect.pin.time.sleep", lambda seconds: None)
    injector = PinInjector("198310gao", str(exe))
    assert injector.press("enter")
    assert calls[-1] == [str(exe), "send", "-k", "Enter"]


def test_pin_window_detection(monkeypatch):
    import sys
    import types

    fake = types.SimpleNamespace(
        getAllWindows=lambda: [
            types.SimpleNamespace(title="中国工商银行企业网上银行 - Google Chrome"),
            types.SimpleNamespace(title="输入密码"),
        ]
    )
    monkeypatch.setitem(sys.modules, "pygetwindow", fake)
    assert PinInjector.has_window(("输入密码", "校验U盾密码"))
    assert not PinInjector.has_window(("第二个密码",))


def test_archive_sniffs_guid_temp_files(tmp_path):
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active.append(["交易日期", "借方发生额", "贷方发生额", "账户余额"])
    raw = tmp_path / "raw.xlsx"
    workbook.save(raw)
    guid = tmp_path / "3f2a8f9e-1c2b-4d3e-9f00-aa11bb22cc33"
    raw.rename(guid)

    archived = archive_file(guid, tmp_path / "statements", "工商银行_银行流水", account_suffix="1001")
    assert archived.suffix == ".xlsx"
    assert archived.exists()

    pdf = tmp_path / "5a1b2c3d-0000-1111-2222-333344445555"
    pdf.write_bytes(b"%PDF-1.4 test")
    archived_pdf = archive_file(pdf, tmp_path / "receipts", "工商银行_电子回单")
    assert archived_pdf.suffix == ".pdf"


def test_profiles_cover_driver_selectors():
    from common.collect.profile import BankProfile

    core_keys = {"date_start_input", "date_end_input", "no_data", "confirm_button", "logged_in"}
    for bank in ("citic", "cib", "icbc"):
        profile = BankProfile.load(bank, profile_path(bank))
        source = (bank_dir(bank) / "driver.py").read_text(encoding="utf-8")
        keys = set(re.findall(r'sel\("([a-z0-9_]+)"\)', source)) | core_keys
        missing = [key for key in sorted(keys) if not profile.selectors(key)]
        assert not missing, f"{bank}.yaml 缺少选择器: {missing}"
        assert profile.url("login"), f"{bank}.yaml 缺少登录地址"
        assert profile.get(f"file_prefix.statement"), f"{bank}.yaml 缺少文件名前缀"
