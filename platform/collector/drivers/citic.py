from __future__ import annotations

import time

from collector.core import flow, ui
from collector.core.archive import archive_file
from collector.core.browser import BrowserSession
from collector.core.download import HTML_SUFFIXES
from collector.core.result import CollectResult, ErrorCode
from collector.drivers.base import BankDriver, RunContext


class CiticDriver(BankDriver):
    bank_code = "citic"
    display_name = "中信银行"

    def _prefix(self, kind: str) -> str:
        return self.profile.get(f"file_prefix.{kind}", f"{self.display_name}_{kind}")

    def _focus_cleanup(self, page) -> None:
        try:
            page.mouse.click(200, 200)
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
        except Exception:
            pass

    def _close_announcement(self, page) -> None:
        if ui.any_visible(page, self.sel("announcement_close"), timeout=800):
            flow.close_popups(page, self)
        try:
            page.evaluate(
                """() => {
                    const el = document.getElementById('advertPop');
                    if (el) el.style.display = 'none';
                    document.querySelectorAll('.modal-backdrop').forEach(n => n.remove());
                }"""
            )
        except Exception:
            pass
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

    def _activate_tab(self, page, key: str) -> bool:
        locator = ui.find(page, self.sel(key), timeout=2000)
        if locator is None:
            return False
        try:
            classes = locator.get_attribute("class") or ""
            if "active" in classes:
                return True
        except Exception:
            pass
        return ui.click(page, self.sel(key), timeout=2000)

    def _inject_pin(self, ctx: RunContext, page) -> bool:
        selectors = self.sel("password_input")
        for _ in range(3):
            if ui.exists(page, selectors):
                break
            page.wait_for_timeout(1000)
        ui.scroll_into_view(page, selectors)
        ui.click(page, selectors, timeout=3000)
        order = list(self.option("pin_order", ["aikeyboard", "fill", "js", "pyautogui"]))
        if ctx.pin.inject(page, selectors, order=order):
            ctx.log("PIN 已注入")
            return True
        ctx.log("常规 PIN 注入失败，尝试 JS 聚焦后重试")
        try:
            page.evaluate(
                """() => {
                    const el = document.getElementById('passwordId') || document.querySelector('input[type=password]');
                    if (el) { el.focus(); el.click(); }
                }"""
            )
        except Exception:
            pass
        page.wait_for_timeout(1000)
        return ctx.pin.aikeyboard() or ctx.pin.fill(page, selectors) or ctx.pin.js_set(page, selectors)

    def _login_with_password(self, ctx: RunContext, page) -> bool:
        username = ctx.credentials.get("username", "")
        password = ctx.credentials.get("password", "")
        if not username or not password:
            return False
        if not ui.any_visible(page, self.sel("username_input"), timeout=1500):
            return False
        ctx.log("尝试用户名/密码登录")
        self._activate_tab(page, "username_tab")
        ui.fill(page, self.sel("username_input"), username)
        ui.fill(page, self.sel("password_input_alt"), password)
        ui.click(page, self.sel("login_button"), timeout=3000)
        page.wait_for_timeout(3000)
        return flow.wait_logged_in(page, self, timeout=20, url_hint="cotb")

    def login(self, ctx: RunContext, session: BrowserSession) -> bool:
        page = session.page
        login_url = self.url("login")
        try:
            page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"登录页加载超时，继续尝试: {exc}")
        page.wait_for_timeout(5000)
        self._focus_cleanup(page)
        self._close_announcement(page)

        if ui.any_visible(page, self.sel("ukey_tab"), timeout=2000):
            ctx.log("切换到 U 盾登录")
            self._activate_tab(page, "ukey_tab")
            page.wait_for_timeout(1000)

        if ctx.pin and ctx.pin.pin:
            self._inject_pin(ctx, page)
            page.wait_for_timeout(2000)

        if flow.wait_logged_in(page, self, timeout=10, url_hint="cotb"):
            ctx.log("检测到已登录")
            return True

        if ui.click(page, self.sel("login_button"), timeout=3000):
            ctx.log("点击登录按钮")
        else:
            ctx.log("未找到登录按钮，等待客户端证书完成登录")
        page.wait_for_timeout(3000)

        if flow.wait_logged_in(page, self, timeout=30, url_hint="cotb"):
            return True
        if self._login_with_password(ctx, page):
            return True

        ctx.log("自动登录未完成，结束任务（全自动模式，不等待人工）", level=30)
        return flow.wait_logged_in(page, self, timeout=20, url_hint="cotb")

    def _goto(self, page, url: str, marker_key: str, timeout: int = 60) -> bool:
        if not url:
            return False
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        page.wait_for_timeout(5000)
        self._close_announcement(page)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if ui.any_visible(page, self.sel(marker_key), timeout=800):
                flow.close_sidebar(page, self)
                return True
            page.wait_for_timeout(1000)
        return False

    def _query(self, page, key: str) -> bool:
        selectors = self.sel(key)
        clicked = ui.click_js(page, selectors, timeout=3000) or ui.click(page, selectors, timeout=3000)
        if clicked:
            page.wait_for_timeout(3000)
        return clicked

    def _download(self, ctx: RunContext, page, kind: str, account: str, retries: int = 1) -> CollectResult:
        timeout = int(self.option("download_timeout", 60))
        before = flow.snapshot_downloads(ctx)
        started = time.time()

        if kind == "statement":
            if not (ui.click(page, self.sel("download_all_toggle"), timeout=5000) or ui.click(page, self.sel("download_generic"), timeout=3000)):
                return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="未找到全部下载入口")
            page.wait_for_timeout(2000)
            option_key = "download_excel_option"
        else:
            ui.click(page, self.sel("select_all_checkbox"), timeout=3000)
            if not (ui.click(page, self.sel("download_dropdown"), timeout=5000) or ui.click(page, self.sel("download_generic"), timeout=3000)):
                return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="未找到全部下载入口")
            page.wait_for_timeout(2000)
            option_key = "download_pdf_option"

        option_selectors = self.sel(option_key)
        if not (ui.click_js(page, option_selectors, timeout=3000) or ui.click(page, option_selectors, timeout=5000)):
            return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="未找到下载格式选项")
        page.wait_for_timeout(3000)
        flow.confirm_dialog(page, self, timeout=3000, native_fallback=True)

        found = flow.wait_download(ctx, before, timeout=timeout)
        if found is None:
            ctx.log("未检测到新文件，回退取最近生成的下载文件")
            found = flow.latest_after(ctx, started)
        if found is None:
            return CollectResult(error_code=ErrorCode.DOWNLOAD_FAILED, message="等待下载超时")
        if found.suffix.lower() in HTML_SUFFIXES:
            ctx.log(f"下载到 HTML 页面而非文件: {found.name}")
            found.unlink(missing_ok=True)
            if retries > 0:
                page.wait_for_timeout(3000)
                return self._download(ctx, page, kind, account, retries=retries - 1)
            return CollectResult(error_code=ErrorCode.DOWNLOAD_FAILED, message="下载内容为 HTML 页面")

        archived = archive_file(
            found,
            ctx.archive_root / ("statements" if kind == "statement" else "receipts"),
            self._prefix(kind),
            account_suffix=account,
            archive_by_month=bool(self.option("archive_by_month", True)),
        )
        ctx.log(f"{'流水' if kind == 'statement' else '回单'}已归档: {archived}")
        return CollectResult(files=[archived])

    def collect_statements(self, ctx: RunContext, session: BrowserSession, account: str, start: str, end: str) -> CollectResult:
        page = session.page
        if not self._goto(page, self.url("statement"), "statement_page_marker"):
            return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="未能进入账户明细查询页面")
        flow.handle_survey(page, self)
        if not flow.set_date_range(page, self, start, end):
            return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="设置查询日期失败")
        if not self._query(page, "query_button_statement"):
            return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="未找到查询按钮")
        if not flow.has_data(page, self):
            ctx.log("查询结果为空")
            return CollectResult(no_data=True)
        return self._download(ctx, page, "statement", account)

    def collect_receipts(self, ctx: RunContext, session: BrowserSession, account: str, start: str, end: str) -> CollectResult:
        page = session.page
        if not self._goto(page, self.url("receipt"), "receipt_page_marker"):
            return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="未能进入电子回单页面")
        flow.close_popups(page, self)
        if not flow.set_date_range(page, self, start, end):
            return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="设置查询日期失败")
        if not self._query(page, "query_button_receipt"):
            return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="未找到查询按钮")
        if not flow.has_data(page, self):
            ctx.log("回单查询结果为空")
            return CollectResult(no_data=True)
        return self._download(ctx, page, "receipt", account)
