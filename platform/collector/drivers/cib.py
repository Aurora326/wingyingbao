from __future__ import annotations

import time
from pathlib import Path

from collector.core import flow, ui
from collector.core.archive import archive_file
from collector.core.browser import BrowserSession
from collector.core.download import HTML_SUFFIXES
from collector.core.result import CollectResult, ErrorCode
from collector.drivers.base import BankDriver, RunContext


class CibDriver(BankDriver):
    bank_code = "cib"
    display_name = "兴业银行"

    def _prefix(self, kind: str) -> str:
        return self.profile.get(f"file_prefix.{kind}", f"{self.display_name}_{kind}")

    def _frame_by_id(self, page, frame_id: str):
        for frame in page.frames:
            try:
                if frame.frame_element().get_attribute("id") == frame_id:
                    return frame
            except Exception:
                continue
        return None

    def _frame_by_hints(self, page, url_hint: str, name_hint: str):
        for frame in page.frames:
            if url_hint and url_hint in (frame.url or ""):
                return frame
        for frame in page.frames:
            if name_hint and name_hint in (frame.name or ""):
                return frame
        return None

    def _scope_with_markers(self, page, marker_key: str, timeout: int = 25):
        markers = self.sel(marker_key)
        deadline = time.time() + timeout
        while time.time() < deadline:
            for frame in page.frames:
                if ui.exists(frame, markers):
                    return frame
            if ui.exists(page, markers):
                return page
            try:
                page.wait_for_timeout(1000)
            except Exception:
                break
        return None

    def _find_scope(self, page, marker_key: str, timeout: int = 25):
        return self._scope_with_markers(page, marker_key, timeout=timeout)

    def _click_in_any_frame(self, page, selectors: list[str], timeout: int = 3000) -> bool:
        for frame in [page] + list(page.frames):
            if ui.click_js(frame, selectors, timeout=300) or ui.click(frame, selectors, timeout=300):
                return True
        return ui.click(page, selectors, timeout=timeout)

    def _inject_pin(self, ctx: RunContext, page) -> bool:
        ctx.pin.activate_window("Chrome")
        page.wait_for_timeout(1000)
        selectors = self.sel("pin_input") + self.sel("password_input")
        ui.click(page, selectors, timeout=3000)
        page.wait_for_timeout(1000)
        order = list(self.option("pin_order", ["pyautogui", "fill", "js", "aikeyboard"]))
        if ctx.pin.inject(page, selectors, order=order):
            ctx.log("PIN 已注入")
            page.wait_for_timeout(1500)
            return True
        ctx.log("PIN 注入失败，等待人工输入", level=30)
        return False

    def _login_with_password(self, ctx: RunContext, page) -> bool:
        username = ctx.credentials.get("username", "")
        password = ctx.credentials.get("password", "")
        if not username or not password:
            return False
        locator = ui.find(page, self.sel("login_name_input"), timeout=3000)
        if locator is None:
            return False
        ctx.log(f"使用用户名登录: {username}")
        ui.click(page, self.sel("login_name_input"), timeout=2000)
        page.wait_for_timeout(500)
        if not ui.click(page, [f"text={username}"], timeout=2000):
            ui.fill(page, self.sel("login_name_input"), username)
            page.keyboard.press("Enter")
            page.wait_for_timeout(500)
        ui.fill(page, self.sel("login_password_input"), password)
        if ui.exists(page, self.sel("agree_checkbox")):
            try:
                checkbox = page.locator(self.sel("agree_checkbox")[0]).first
                if not checkbox.is_checked():
                    checkbox.check(timeout=2000)
            except Exception:
                pass
        ui.click(page, self.sel("login_button"), timeout=3000)
        page.wait_for_timeout(3000)
        return flow.wait_logged_in(page, self, timeout=30)

    def login(self, ctx: RunContext, session: BrowserSession) -> bool:
        page = session.page
        try:
            page.goto(self.url("login"), wait_until="networkidle", timeout=60000)
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"登录页加载异常，继续尝试: {exc}")
        page.wait_for_timeout(3000)

        if ctx.pin and ctx.pin.pin:
            self._inject_pin(ctx, page)
        flow.confirm_dialog(page, self, timeout=3000, native_fallback=True)
        page.wait_for_timeout(3000)

        if flow.wait_logged_in(page, self, timeout=10):
            ctx.log("检测到已登录")
            return True
        if self._login_with_password(ctx, page):
            return True

        ctx.log("自动登录未完成，结束任务（全自动模式，不等待人工）", level=30)
        return flow.wait_logged_in(page, self, timeout=20)

    def _open_statement(self, page) -> object | None:
        quick = self.sel("statement_quick_entry")
        deadline = time.time() + 30
        while time.time() < deadline:
            for frame in page.frames:
                locator = ui.find(frame, quick, timeout=300, require_visible=False)
                if locator is not None:
                    try:
                        locator.click(timeout=2000)
                    except Exception:
                        continue
                    page.wait_for_timeout(3000)
                    return self._find_scope(page, "main_frame_markers")
            page.wait_for_timeout(1000)

        if not self._click_in_any_frame(page, self.sel("statement_menu")):
            return None
        page.wait_for_timeout(4000)
        return self._find_scope(page, "main_frame_markers")

    def _react_scope(self, page):
        frame_id = self.profile.get("frames.react_app_id", "react-app")
        return self._frame_by_id(page, frame_id) or page

    def _click_menu_step(self, page, scope, key: str) -> bool:
        selectors = self.sel(key)
        if ui.click(scope, selectors, timeout=2000):
            return True
        for frame in page.frames:
            if ui.click(frame, selectors, timeout=300):
                return True
        return ui.click(page, selectors, timeout=1000)

    def _open_receipt(self, page, ctx: RunContext) -> object | None:
        page.wait_for_timeout(2000)
        scope = self._react_scope(page)
        if self.option("sidebar_scroll", True):
            try:
                scope.evaluate(
                    """() => {
                        const el = document.querySelector('.bottom-menu.bottom-menu-min') || document.querySelector('.ant-layout-sider');
                        if (el) el.scrollTo(0, el.scrollHeight);
                        else window.scrollTo(0, document.body.scrollHeight);
                    }"""
                )
            except Exception:
                pass
            page.wait_for_timeout(800)

        ui.click(scope, self.sel("menu_account_query"), timeout=1500)
        page.wait_for_timeout(800)

        for key in ("receipt_menu_step1", "receipt_menu_step2", "receipt_menu_step3"):
            clicked = False
            for _ in range(3):
                if self._click_menu_step(page, scope, key):
                    clicked = True
                    break
                page.wait_for_timeout(1000)
            if not clicked:
                ctx.log(f"回单菜单未点中: {key}", level=30)
                return None
            page.wait_for_timeout(2000)

        page.wait_for_timeout(5000)
        url_hint = self.profile.get("frames.receipt_url_hint", "")
        name_hint = str(self.profile.get("frames.receipt_name_hint", ""))
        frame = self._frame_by_hints(page, url_hint, name_hint)
        if frame is not None:
            return frame
        return self._find_scope(page, "main_frame_markers")

    def _select_account(self, ctx: RunContext, scope, account: str) -> None:
        if not account:
            return
        if ui.any_visible(scope, self.sel("account_dropdown"), timeout=2000):
            ui.click(scope, self.sel("account_dropdown"), timeout=2000)
            try:
                scope.wait_for_timeout(800)
            except Exception:
                pass
        if ui.click(scope, [f"text={account}"], timeout=2000):
            ctx.log(f"已切换账户: {account}")
            try:
                scope.wait_for_timeout(1500)
            except Exception:
                pass
        else:
            ctx.log(f"未能切换账户 {account}，按页面当前账户继续", level=30)

    def _save_download(self, ctx: RunContext, page, before: dict[str, float], click, timeout: int) -> Path | None:
        started = time.time()
        triggered = False
        try:
            with page.expect_download(timeout=10000) as info:
                click()
                triggered = True
            download = info.value
            name = download.suggested_filename or f"cib_{int(time.time())}.bin"
            target = ctx.downloads_dir / name
            download.save_as(str(target))
            ctx.log(f"下载完成: {target.name}")
            return target.resolve()
        except Exception:
            if not triggered:
                try:
                    click()
                except Exception:
                    pass
        found = flow.wait_download(ctx, before, timeout=timeout)
        if found is None:
            found = flow.latest_after(ctx, started)
        return found

    def _archive(self, ctx: RunContext, found: Path, kind: str, account: str) -> Path:
        return archive_file(
            found,
            ctx.archive_root / ("statements" if kind == "statement" else "receipts"),
            self._prefix(kind),
            account_suffix=account,
            archive_by_month=bool(self.option("archive_by_month", True)),
        )

    def _collect_statement_once(self, ctx: RunContext, page, account: str, start: str, end: str) -> CollectResult:
        scope = self._open_statement(page)
        if scope is None:
            return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="未找到账户明细查询入口")
        self._select_account(ctx, scope, account)
        if not flow.set_date_range(scope, self, start, end):
            return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="设置查询日期失败")
        if not (ui.click_js(scope, self.sel("query_button"), timeout=2000) or ui.click(scope, self.sel("query_button"), timeout=5000)):
            return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="未找到查询按钮")
        page.wait_for_timeout(3000)
        if not flow.has_data(scope, self):
            ctx.log("查询结果为空")
            return CollectResult(no_data=True)

        before = flow.snapshot_downloads(ctx)
        if not ui.click(scope, self.sel("download_menu"), timeout=5000):
            return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="未找到下载入口")
        page.wait_for_timeout(1000)
        if not ui.click(scope, self.sel("excel_option"), timeout=5000):
            return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="未找到 Excel 下载选项")
        page.wait_for_timeout(2000)
        flow.confirm_dialog(page, self, timeout=4000)

        link = flow.wait_for_download_link(page, self, timeout=int(self.option("download_timeout", 120)))
        if link is None:
            return CollectResult(error_code=ErrorCode.DOWNLOAD_FAILED, message="打包下载文件未生成")
        found = self._save_download(ctx, page, before, link.click, timeout=60)
        if found is None:
            return CollectResult(error_code=ErrorCode.DOWNLOAD_FAILED, message="等待下载超时")
        if found.suffix.lower() in HTML_SUFFIXES:
            ctx.log(f"下载到 HTML 页面而非文件: {found.name}")
            found.unlink(missing_ok=True)
            return CollectResult(error_code=ErrorCode.DOWNLOAD_FAILED, message="下载内容为 HTML 页面")
        archived = self._archive(ctx, found, "statement", account)
        ctx.log(f"流水已归档: {archived}")
        return CollectResult(files=[archived])

    def collect_statements(self, ctx: RunContext, session: BrowserSession, account: str, start: str, end: str) -> CollectResult:
        page = session.page
        result = self._collect_statement_once(ctx, page, account, start, end)
        if result.error_code == ErrorCode.DOWNLOAD_FAILED and "HTML" in result.message:
            ctx.log("HTML 误下，重新查询并重试一次")
            page.wait_for_timeout(5000)
            result = self._collect_statement_once(ctx, page, account, start, end)
        return result

    def collect_receipts(self, ctx: RunContext, session: BrowserSession, account: str, start: str, end: str) -> CollectResult:
        page = session.page
        scope = self._open_receipt(page, ctx)
        if scope is None:
            return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="未找到电子回单查询入口")
        if not flow.set_date_range(scope, self, start, end):
            return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="设置查询日期失败")
        if not (ui.click_js(scope, self.sel("query_button"), timeout=2000) or ui.click(scope, self.sel("query_button"), timeout=5000)):
            return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="未找到查询按钮")
        page.wait_for_timeout(4000)
        if not flow.has_data(scope, self):
            ctx.log("回单查询结果为空")
            return CollectResult(no_data=True)

        before = flow.snapshot_downloads(ctx)
        if not ui.click(scope, self.sel("receipt_select_all"), timeout=3000):
            ctx.log("未找到全选复选框，尝试直接下载")
        if not ui.click(scope, self.sel("receipt_download_button"), timeout=5000):
            return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="未找到回单下载入口")
        page.wait_for_timeout(2000)

        dialog_id = self.profile.get("frames.dialog_id", "dialogFrame")
        dialog_scope = self._frame_by_id(page, dialog_id) or page
        pdf_selectors = self.sel("receipt_pdf_button")
        if not (ui.click_js(dialog_scope, pdf_selectors, timeout=2000) or ui.click(dialog_scope, pdf_selectors, timeout=5000)):
            return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="未找到 PDF 下载按钮")
        page.wait_for_timeout(1500)
        flow.confirm_dialog(page, self, timeout=5000, native_fallback=True)

        found = flow.wait_download(ctx, before, timeout=int(self.option("download_timeout", 120)))
        if found is None:
            return CollectResult(error_code=ErrorCode.DOWNLOAD_FAILED, message="等待回单下载超时")
        if found.suffix.lower() in HTML_SUFFIXES:
            found.unlink(missing_ok=True)
            return CollectResult(error_code=ErrorCode.DOWNLOAD_FAILED, message="下载内容为 HTML 页面")
        archived = self._archive(ctx, found, "receipt", account)
        ctx.log(f"回单已归档: {archived}")
        return CollectResult(files=[archived])
