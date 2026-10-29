from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from collector.core import ui
from collector.core.download import DownloadWatcher, newest_since, snapshot


def close_popups(scope: Any, driver) -> None:
    for key in ("announcement_close", "survey_close", "excel_dialog_close"):
        for selector in driver.sel(key):
            try:
                locator = scope.locator(selector).first
                if locator.is_visible(timeout=500):
                    locator.click(timeout=1000, force=True)
                    scope.wait_for_timeout(300)
            except Exception:
                continue


def close_sidebar(page: Any, driver) -> None:
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    point = driver.option("sidebar_close_click", [600, 400])
    try:
        page.mouse.click(int(point[0]), int(point[1]))
    except Exception:
        pass


def is_logged_in(page: Any, driver, url_hint: str = "") -> bool:
    try:
        if ui.any_visible(page, driver.sel("logged_in"), timeout=300):
            return True
        if url_hint:
            current = page.url.lower()
            if url_hint in current and "login" not in current:
                return True
    except Exception:
        return False
    return False


def wait_logged_in(page: Any, driver, timeout: int = 90, url_hint: str = "") -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_logged_in(page, driver, url_hint):
            return True
        try:
            page.wait_for_timeout(800)
        except Exception:
            return False
    return False


JS_SET_DATE = """([selectors, value]) => {
    for (const sel of selectors) {
        let el = null;
        try { el = document.querySelector(sel); } catch (e) { continue; }
        if (!el) continue;
        el.removeAttribute('readonly');
        el.value = value;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        return true;
    }
    return false;
}"""


def _set_one(scope: Any, selectors: list[str], value: str) -> bool:
    try:
        if scope.evaluate(JS_SET_DATE, [selectors, value]):
            return True
    except Exception:
        pass
    return ui.fill(scope, selectors, value, timeout=2000)


def set_date_range(scope: Any, driver, start: str, end: str) -> bool:
    if driver.option("date_click_other", False):
        ui.click(scope, driver.sel("date_other"), timeout=2000)
        try:
            scope.wait_for_timeout(800)
        except Exception:
            pass
    start_ok = _set_one(scope, driver.sel("date_start_input"), start)
    end_ok = _set_one(scope, driver.sel("date_end_input"), end)
    return start_ok and end_ok


def has_data(scope: Any, driver) -> bool:
    return not ui.any_visible(scope, driver.sel("no_data"), timeout=800)


def handle_survey(page: Any, driver) -> None:
    if not driver.sel("survey_indicator"):
        return
    if not ui.any_visible(page, driver.sel("survey_indicator"), timeout=1500):
        return
    if ui.any_visible(page, driver.sel("survey_option"), timeout=1000):
        ui.click(page, driver.sel("survey_option"), timeout=2000)
        page.wait_for_timeout(800)
    if ui.any_visible(page, driver.sel("survey_submit"), timeout=1000):
        ui.click(page, driver.sel("survey_submit"), timeout=2000)
    close_popups(page, driver)
    page.wait_for_timeout(2000)


JS_CONFIRM = """() => {
    const boxes = Array.from(document.querySelectorAll('.ui-dialog-buttonpane, .el-message-box, .modal, .ant-modal'));
    for (const box of boxes.reverse()) {
        if (!box || box.offsetParent === null) continue;
        const buttons = Array.from(box.querySelectorAll('button, a, span'));
        for (const btn of buttons.reverse()) {
            const text = (btn.textContent || '').trim();
            if (text === '确定' || text === '确认' || text === 'OK' || text === '继续下载') {
                btn.click();
                return true;
            }
        }
    }
    return false;
}"""


def confirm_dialog(page: Any, driver, timeout: int = 5000, native_fallback: bool = False) -> bool:
    selectors = list(driver.sel("confirm_button")) + [
        ".ui-dialog-buttonpane.type-confirm .ui-dialog-buttonset button:last-child",
        ".ui-dialog-buttonpane button:nth-child(2)",
        ".el-message-box__btns .el-button--primary",
    ]
    scopes = [page] + [frame for frame in getattr(page, "frames", []) if frame is not page.main_frame]
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        for scope in scopes:
            try:
                if not ui.exists(scope, [".ui-dialog-buttonpane", ".el-message-box", ".modal", ".ant-modal"]):
                    continue
            except Exception:
                continue
            if ui.click_js(scope, selectors, timeout=1000):
                return True
            try:
                if scope.evaluate(JS_CONFIRM):
                    return True
            except Exception:
                pass
        try:
            page.wait_for_timeout(500)
        except Exception:
            break
    if native_fallback:
        try:
            import pyautogui

            pyautogui.press("enter")
            return True
        except Exception:
            return False
    return False


COUNTDOWN_RE = re.compile(r"再次下载请等待[^\d]*(\d+)")


def wait_for_download_link(page: Any, driver, timeout: int = 120):
    link_selectors = driver.sel("download_link")
    deadline = time.time() + timeout
    while time.time() < deadline:
        for frame in [page] + [f for f in getattr(page, "frames", []) if f is not page.main_frame]:
            locator = ui.find(frame, link_selectors, timeout=500, require_visible=False)
            if locator is None:
                continue
            try:
                disabled = locator.evaluate(
                    "el => el.disabled || el.classList.contains('disabled') || el.getAttribute('aria-disabled') === 'true'"
                )
            except Exception:
                disabled = False
            if not disabled:
                return locator
        try:
            text = page.locator("body").inner_text(timeout=1000)
        except Exception:
            text = ""
        match = COUNTDOWN_RE.search(text)
        if match:
            try:
                page.wait_for_timeout(min(int(match.group(1)) + 2, 10) * 1000)
            except Exception:
                pass
        try:
            page.wait_for_timeout(1000)
        except Exception:
            break
    return None


def download_dirs(ctx) -> list[Path]:
    dirs = [ctx.downloads_dir, Path.home() / "Downloads"]
    configured = ctx.profile.get("options.download_path")
    if configured:
        dirs.append(Path(configured))
    return dirs


def wait_download(ctx, before: dict[str, float], timeout: int = 90) -> Path | None:
    watcher = DownloadWatcher(download_dirs(ctx), timeout=timeout, logger=ctx.logger)
    return watcher.wait(before)


def snapshot_downloads(ctx) -> dict[str, float]:
    return snapshot(download_dirs(ctx))


def latest_after(ctx, started: float) -> Path | None:
    return newest_since(download_dirs(ctx), started)


def log_step(ctx, message: str) -> None:
    ctx.log(message)
