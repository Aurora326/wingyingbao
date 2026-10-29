from __future__ import annotations

import datetime as dt
import io
import time
from contextlib import contextmanager

from common.collect import flow, ui, windows_ui
from common.collect.archive import archive_file
from common.collect.browser import BrowserSession
from common.collect.download import HTML_SUFFIXES
from common.collect.result import CollectResult, ErrorCode
from common.collect.base import BankDriver, RunContext


class IcbcDriver(BankDriver):
    bank_code = "icbc"
    display_name = "工商银行"
    CERT_TITLES = ("选择证书", "证书")
    # 工行中间件 MWICBCUKeyUI.exe 的密码框标题有「输入密码」也有「操作提示」，
    # 后者容易被误认成普通提示，必须一并当作密码弹窗处理
    PIN_TITLES = ("输入密码", "校验U盾密码", "U盾密码", "操作提示")
    # 「用户提示」是密码错误后的「是否继续重试」询问框，必须终止而不是重试
    ERROR_TITLES = ("用户提示",)

    def _handle_error_dialog(self, ctx: RunContext) -> bool:
        """识别并关闭 U盾中间件的系统提示，绝不重试，避免继续消耗剩余次数。"""
        hwnd = windows_ui.find_hwnd(self.ERROR_TITLES)
        if hwnd is None:
            return False
        message = windows_ui.read_dialog_text(self.ERROR_TITLES, timeout=0.5)
        process = windows_ui.window_process_name(hwnd).lower()
        # 优先按进程判断（中间件 MWICBCUKeyUI.exe），正文读不到时也不会漏判
        if "icbc" not in process and not any(k in message for k in ("U盾", "密码", "重试", "Key")):
            return False
        ctx.log(f"检测到系统提示(进程={process or '未知'}): {message.strip()[:100]}", level=30)
        # 有「否/取消」就选它，表示不再重试
        clicked = windows_ui.click_dialog_button(
            self.ERROR_TITLES, ("否", "取消", "关闭"), ratio=(0.72, 0.5), timeout=2
        )
        ctx.log(f"已关闭系统提示: {clicked}", level=30)
        return True

    def _prefix(self, kind: str) -> str:
        return self.profile.get(f"file_prefix.{kind}", f"{self.display_name}_{kind}")

    # ---------- 登录 ----------

    def _click_ukey_login(self, ctx: RunContext, page) -> bool:
        # 弹窗一旦弹出，渲染进程进入模态状态，任何 Playwright 调用都会永久挂起，
        # 所以每一步操作前都要先确认没有弹窗。
        t0 = time.time()
        if self._popup_open():
            ctx.log("U盾弹窗已出现，跳过页面点击，直接交给弹窗处理")
            return True
        t_popup = time.time()
        precise = str(self.option("login_button_selector", ""))
        selectors = ([precise] if precise else []) + [
            "text=U盾登录",
            "button:has-text('U盾登录')",
            "a:has-text('U盾登录')",
        ]
        clicked = False
        with self._short_timeout(page):
            clicked = ui.click(page, selectors, timeout=1500)
        t_click = time.time()
        if clicked:
            ctx.log(f"已点击 U盾登录按钮（页面元素） 检测{t_popup-t0:.1f}s 点击{t_click-t_popup:.1f}s")
            return True
        if self._popup_open():
            ctx.log("点击后 U盾弹窗出现，停止页面操作")
            return True
        try:
            with self._short_timeout(page):
                clicked = page.evaluate(
                    """() => {
                        const nodes = Array.from(document.querySelectorAll('button,a,span,div'));
                        const el = nodes.find(n => (n.textContent || '').trim() === 'U盾登录');
                        if (!el) return false;
                        el.click();
                        return true;
                    }"""
                )
            if clicked:
                ctx.log(f"已点击 U盾登录按钮（JS） 页面点击耗时{t_click-t_popup:.1f}s JS耗时{time.time()-t_click:.1f}s")
                return True
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"JS 点击 U盾登录失败: {exc} 页面点击耗时{t_click-t_popup:.1f}s JS耗时{time.time()-t_click:.1f}s", level=30)
        if self._popup_open():
            ctx.log("JS 点击后 U盾弹窗出现，停止页面操作")
            return True

        ratio = self.option("login_button_ratio", [0.70, 0.46])
        if ctx.pin and ctx.pin.click_ratio(float(ratio[0]), float(ratio[1])):
            ctx.log(f"未找到按钮，按屏幕比例兜底点击: {ratio}", level=30)
            return True
        return False

    def _handle_certificate_popup(self, ctx: RunContext) -> None:
        ctx.log("处理证书选择弹窗...")
        titles = self.CERT_TITLES
        confirm_ratio = self.option("cert_confirm_ratio", [0.71, 0.87])
        row_ratio = self.option("cert_row_ratio", [0.57, 0.36])

        if windows_ui.find_hwnd(titles):
            if windows_ui.double_click_in_dialog(titles, float(row_ratio[0]), float(row_ratio[1]), timeout=2):
                ctx.log("已双击证书行")
                time.sleep(0.8)
                windows_ui.click_in_dialog(titles, float(confirm_ratio[0]), float(confirm_ratio[1]), timeout=1)
            elif windows_ui.click_dialog_button(titles, ("确定", "确认", "OK"), ratio=confirm_ratio, timeout=1.5):
                ctx.log("已点击证书弹窗的「确定」")
            time.sleep(1)
            return

        if windows_ui.click_chrome_dialog_button(titles, ("确定", "确认", "OK"), timeout=8):
            ctx.log("已点击证书弹窗（Chrome 内置）的「确定」")
            time.sleep(1)
            return

        ctx.log(f"未找到证书弹窗，回退 Enter；当前窗口: {windows_ui.list_windows(('证书', '密码', 'U盾'))}", level=30)
        ctx.pin.press("enter", times=1)
        time.sleep(1)

    def _handle_pin_popup(self, ctx: RunContext) -> bool:
        if not ctx.pin or not ctx.pin.pin:
            ctx.log("未配置 UKey PIN，等待人工输入", level=30)
            time.sleep(30)
            return False
        titles = self.PIN_TITLES
        field_ratio = self.option("pin_field_ratio", [0.47, 0.68])
        confirm_ratio = self.option("pin_confirm_ratio", [0.25, 0.87])
        close_timeout = float(self.option("pin_close_timeout", 12))

        # 先按坐标点密码框（会把窗口置前，AiKeyboard 才输入到正确位置），
        # 失败再用控件树聚焦兜底
        if not (windows_ui.click_in_dialog(titles, float(field_ratio[0]), float(field_ratio[1]), timeout=8)
                or windows_ui.focus_edit(titles, timeout=2)):
            ctx.log("未找到 U盾密码弹窗", level=30)
            return False
        ctx.pin.clear()
        ctx.log("已点击 U盾密码框，使用 AiKeyboard 输入")
        if not ctx.pin.aikeyboard():
            ctx.log("AiKeyboard 输入失败，尝试 pyautogui 兜底", level=30)
            # 必须先清空：AiKeyboard 超时前可能已输了一半，直接再输会变成重复密码
            ctx.pin.clear()
            ctx.pin.pyautogui()
        time.sleep(float(self.option("pin_settle", 0.3)))
        if windows_ui.click_dialog_button(titles, ("确定", "确认", "OK"), ratio=confirm_ratio, timeout=1):
            ctx.log("已点击密码弹窗的「确定」")

        deadline = time.time() + close_timeout
        while time.time() < deadline:
            if windows_ui.find_hwnd(titles) is None:
                ctx.log("U盾密码已提交，弹窗已关闭")
                return True
            time.sleep(0.2)
        ctx.log(f"U盾密码弹窗 {close_timeout:.0f}s 内未关闭；当前窗口: {windows_ui.list_windows(('密码', 'U盾'))}", level=30)
        return False

    @contextmanager
    def _short_timeout(self, page, ms: int = 2000):
        """页面加载/模态期间，Playwright 内部会按默认 30s 反复重试，
        导致一次点击等 30 秒。这里临时把默认超时压到 ms。"""
        try:
            page.set_default_timeout(ms)
        except Exception:
            pass
        try:
            yield
        finally:
            try:
                page.set_default_timeout(int(self.option("page_timeout", 30)) * 1000)
            except Exception:
                pass

    def _popup_state(self) -> tuple[bool, bool]:
        """返回 (密码弹窗, 证书弹窗)。原生窗口用 Win32 毫秒级枚举，
        Chrome 内置证书弹窗要扫 UIA（较慢），按 chrome_scan_interval 节流。"""
        if windows_ui.find_hwnd(self.PIN_TITLES) is not None:
            return True, False
        if windows_ui.find_hwnd(self.CERT_TITLES) is not None:
            return False, True
        now = time.time()
        interval = float(self.option("chrome_scan_interval", 1.0))
        if now - getattr(self, "_last_chrome_scan", 0.0) < interval:
            return False, getattr(self, "_last_chrome_result", False)
        self._last_chrome_scan = now
        found = windows_ui.find_chrome_dialog(self.CERT_TITLES) is not None
        self._last_chrome_result = found
        return False, found

    def _popup_open(self) -> bool:
        pin, cert = self._popup_state()
        return pin or cert

    def _logged_in(self, page) -> bool:
        # 弹窗存在时渲染进程处于模态状态，此时调用 Playwright 会永久挂起
        if self._popup_open():
            return False
        try:
            return ui.any_visible(page, self.sel("logged_in_strict"), timeout=300)
        except Exception:
            return False

    def _handle_auth_loop(self, ctx: RunContext, page, timeout: int = 240) -> tuple[bool, int]:
        """只用 Windows 原生检测处理证书/密码弹窗；弹窗期间不调用 Playwright（会挂住）"""
        idle_timeout = float(self.option("auth_idle_timeout", 8))
        login_wait = float(self.option("login_wait", 10))
        ctx.log(f"开始检测登录弹窗（最长 {timeout}s，空闲 {idle_timeout:.0f}s 退出）")
        deadline = time.time() + timeout
        handled = 0
        logged_hits = 0
        last_log = 0.0
        last_check = 0.0
        last_progress = time.time()
        while time.time() < deadline:
            if self._handle_error_dialog(ctx):
                ctx.log("U盾提示密码有误或已停止重试，终止登录", level=30)
                return False, handled
            pin_visible, cert_visible = self._popup_state()
            if pin_visible:
                last_progress = time.time()
                handled += 1
                ctx.log(f"检测到第 {handled} 个 U盾密码弹窗")
                closed = self._handle_pin_popup(ctx)
                if not closed and windows_ui.find_hwnd(self.PIN_TITLES) is not None:
                    ctx.log("密码弹窗未关闭，停止自动重试（避免锁 Key）", level=30)
                    break
                continue
            if cert_visible:
                last_progress = time.time()
                self._handle_certificate_popup(ctx)
                continue
            # 弹窗间隙：轮询真实登录态，连续两次确认成功才结束，避免误判
            now = time.time()
            if handled and now - last_check >= float(self.option("login_check_interval", 1.5)):
                last_check = now
                if self._logged_in(page):
                    logged_hits += 1
                    if logged_hits >= 2:
                        ctx.log("页面已进入登录态，结束检测")
                        return True, handled
                else:
                    logged_hits = 0
            if now - last_progress > idle_timeout:
                ctx.log(f"{idle_timeout:.0f}s 内未出现证书/密码弹窗，结束检测")
                break
            if now - last_log >= 5:
                ctx.log(f"等待认证弹窗中: cert={cert_visible} pin={pin_visible} 已处理密码={handled}")
                last_log = now
            time.sleep(0.2)
        # 密码弹窗还挂着说明密码错误，直接判定失败，不能靠页面元素误判
        if windows_ui.find_hwnd(self.PIN_TITLES) is not None:
            return False, handled
        # 弹窗已全部消失，页面恢复可交互，才允许调用 Playwright 确认登录态
        return self._logged_in(page), handled

    def _active_page(self, page):
        try:
            pages = page.context.pages
            if pages and pages[-1] != page:
                return pages[-1]
        except Exception:
            pass
        return page

    def _goto_nowait(self, ctx: RunContext, page, url: str) -> None:
        """发起导航但不等待完成。
        U盾证书弹窗会把渲染进程拖入模态状态，导航永远不提交，
        同步的 goto/wait_for_timeout 会把主线程一起挂死，所以只发不等。"""
        try:
            with self._short_timeout(page):
                page.evaluate("(url) => { window.location.href = url; }", url)
            ctx.log("登录页导航已发起")
        except Exception as exc:  # noqa: BLE001
            # evaluate 在导航开始时通常会报执行上下文销毁，属于正常现象
            ctx.log(f"登录页导航已发起（上下文切换）: {type(exc).__name__}")

    def _wait_popup(self, timeout: float) -> tuple[bool, bool]:
        """轮询等待弹窗出现，最多 timeout 秒；一旦出现立即返回。"""
        deadline = time.time() + timeout
        while True:
            pin, cert = self._popup_state()
            if pin or cert:
                return pin, cert
            if time.time() >= deadline:
                return False, False
            time.sleep(0.1)

    def login(self, ctx: RunContext, session: BrowserSession) -> bool:
        page = session.page

        # 先发起导航（只发不等），但导航一触发 U盾证书弹窗，渲染进程就进入模态状态，
        # 此时任何 Playwright 调用都会永久挂起。所以导航之后只用 Windows 原生接口巡检弹窗。
        self._goto_nowait(ctx, page, self.url("login"))
        pin, cert = self._wait_popup(float(self.option("login_page_wait", 2.5)))

        if pin or cert:
            ctx.log("检测到已存在的登录弹窗，直接处理")
        else:
            if not self._click_ukey_login(ctx, page):
                ctx.log("未找到 U盾登录按钮", level=30)
            pin, cert = self._wait_popup(float(self.option("cert_wait", 10)))
            ctx.log("确认证书弹窗")
            if cert:
                time.sleep(float(self.option("cert_settle", 0.5)))
                self._handle_certificate_popup(ctx)
            elif pin:
                ctx.log("已出现 U盾密码弹窗，交由认证流程处理")
            else:
                # 不再盲目按回车：回车可能把证书弹窗直接取消掉，导致后续没有密码框
                ctx.log("未检测到证书/密码弹窗，交给后续巡检继续等待", level=30)

        ok, handled = self._handle_auth_loop(ctx, page, timeout=int(self.option("auth_timeout", 240)))
        ctx.log(f"登录流程结束: 密码弹窗 {handled} 次, 已登录={ok}")
        return ok

    # ---------- 页面导航 ----------

    def _dismiss_ad_popup(self, ctx: RunContext, page) -> None:
        """关闭首页广告弹窗（#msgFrame 里的全屏遮罩），否则会挡住菜单点击。
        先勾选「不再提示当前广告内容」，再把遮罩隐藏。"""
        popup = None
        for frame in page.frames:
            if "newEiopAlertAd" in frame.url:
                popup = frame
                break
        if popup is None:
            return
        try:
            checkbox = popup.locator(".PopPageFooter img").first
            if checkbox.count() > 0:
                checkbox.click(timeout=1000, force=True)
                ctx.log("已勾选广告弹窗的「不再提示当前广告内容」")
        except Exception:
            pass
        try:
            page.evaluate(
                """() => {
                    const frame = document.getElementById('msgFrame');
                    if (frame) {
                        frame.style.display = 'none';
                        frame.src = 'about:blank';
                    }
                    document.querySelectorAll('.PopPage, .PopPageMask, .PopPageBg').forEach(el => el.remove());
                }"""
            )
            ctx.log("已关闭首页广告弹窗")
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"关闭首页广告弹窗失败: {exc}", level=30)

    def _click_menu(self, page, texts: list[str]) -> bool:
        """按选择器顺序点击菜单。选择器可以是 CSS，也可以是 text= 形式。
        工行左侧菜单项带 hide 类（Playwright 判定不可见）但仍能被 JS 触发，
        所以元素存在且可见时正常点击，否则退化为 JS 点击。"""
        for frame in page.frames:
            for selector in texts:
                if not selector:
                    continue
                try:
                    if selector.startswith("text="):
                        keyword = selector[5:]
                        locator = frame.get_by_text(keyword, exact=True).first
                        if locator.count() == 0:
                            locator = frame.get_by_text(keyword, exact=False).first
                    else:
                        locator = frame.locator(selector).first
                    if locator.count() == 0:
                        continue
                    try:
                        ok = locator.is_visible()
                    except Exception:
                        ok = False
                    if ok:
                        try:
                            locator.click(timeout=3000)
                            return True
                        except Exception:
                            pass
                    locator.evaluate("el => el.click()")
                    return True
                except Exception:
                    continue
        return False

    def _find_content_root(self, page):
        for frame in page.frames:
            try:
                if frame == page.main_frame:
                    continue
                if frame.locator(
                    "input[placeholder*='日期'], input[name*='date'], input[name*='Date'],"
                    " input[id*='beginDate'], input[id*='endDate']"
                ).count() > 0:
                    return frame
            except Exception:
                continue
        return page

    def _open_menu(self, ctx: RunContext, page, marker_key: str, root_timeout: int = 30):
        """展开一级导航并点击业务菜单，然后在 iframe 中定位业务页面"""
        page = self._active_page(page)
        self._dismiss_ad_popup(ctx, page)
        nav = self.sel(f"{marker_key}_nav")
        if nav and self._click_menu(page, nav):
            ctx.log(f"已展开导航: {nav[0]}")
            page.wait_for_timeout(1500)
        if not self._click_menu(page, self.sel(f"{marker_key}_menu")):
            ctx.log(f"未找到菜单: {self.sel(f'{marker_key}_menu')}", level=30)
            return None
        ctx.log("菜单已点击，等待业务页面加载")
        page.wait_for_timeout(5000)
        page = self._active_page(page)
        deadline = time.time() + root_timeout
        while time.time() < deadline:
            page = self._active_page(page)
            root = self._find_content_root(page)
            if ui.any_visible(root, self.sel("page_marker"), timeout=800):
                ctx.log("已进入业务页面")
                flow.close_sidebar(page, self)
                return page, root
            ctx.log(
                f"等待业务页面: url={root.url if hasattr(root, 'url') else '?'} "
                f"页面数={len(page.context.pages)} frames={[f.url[:60] for f in page.frames][:6]}"
            )
            page.wait_for_timeout(2000)
        ctx.log(f"{root_timeout}s 内未进入业务页面", level=30)
        return None

    JS_SET_DATE_RANGE = """([begin, end]) => {
        const q = (s) => document.querySelector(s);
        const set = (sel, val) => {
            const el = q(sel);
            if (!el) return false;
            el.removeAttribute('readonly');
            el.value = val;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
        };
        // 可见输入框是控件，真正提交的是隐藏字段，两者都要写
        const bVisible = set('#datepicker1-beginDate', begin);
        const eVisible = set('#datepicker1-endDate', end);
        const bHidden = set("input[name='Begin_date']", begin);
        const eHidden = set("input[name='End_date']", end);
        return {
            bVisible, eVisible, bHidden, eHidden,
            beginVisible: q('#datepicker1-beginDate') && q('#datepicker1-beginDate').value,
            endVisible: q('#datepicker1-endDate') && q('#datepicker1-endDate').value,
            beginHidden: q("input[name='Begin_date']") && q("input[name='Begin_date']").value,
            endHidden: q("input[name='End_date']") && q("input[name='End_date']").value,
            beginTime: q("input[name='Begin_time']") && q("input[name='Begin_time']").value,
            endTime: q("input[name='End_time']") && q("input[name='End_time']").value,
        };
    }"""

    JS_SET_DATE_WIDGET = """([selector, y, m, d]) => {
        const el = document.querySelector(selector);
        if (!el) return 'no-el';
        const $ = window.jQuery || window.$;
        if (!$) return 'no-jquery';
        try {
            const jq = $(el);
            if (typeof jq.datepicker !== 'function') return 'no-datepicker';
            jq.datepicker('setDate', new Date(y, m - 1, d));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return 'ok:' + el.value;
        } catch (e) {
            return 'err:' + (e && e.message);
        }
    }"""

    def _set_date_widget(self, root, selector: str, value: str) -> str:
        """用 datepicker 自身的 API 设置日期，必须更新控件内部状态，
        否则提交时会被控件的默认值（今天）覆盖。"""
        try:
            y, m, d = int(value[0:4]), int(value[4:6]), int(value[6:8])
        except (ValueError, IndexError):
            return "bad-value"
        try:
            return str(root.evaluate(self.JS_SET_DATE_WIDGET, [selector, y, m, d]))
        except Exception as exc:  # noqa: BLE001
            return f"exception:{type(exc).__name__}"

    def _set_date_input(self, page, root, selector: str, value: str) -> bool:
        """工行日期控件有内部状态，用 JS 改 value 会在提交时被控件覆盖，
        必须像用户一样聚焦后键盘输入。"""
        try:
            locator = root.locator(selector).first
            if locator.count() == 0:
                return False
            locator.click(timeout=3000)
            page.wait_for_timeout(300)
            page.keyboard.press("Control+A")
            page.keyboard.type(value, delay=40)
            page.wait_for_timeout(200)
            page.keyboard.press("Tab")
            page.wait_for_timeout(300)
            return True
        except Exception:
            return False

    def _query(self, ctx: RunContext, root, page, start: str, end: str) -> bool:
        # 工行日期控件是 YYYYMMDD 格式（任务里传的是 YYYY-MM-DD）
        start_value = start.replace("-", "")
        end_value = end.replace("-", "")
        typed = self._set_date_input(page, root, "#datepicker1-beginDate", start_value)
        typed = self._set_date_input(page, root, "#datepicker1-endDate", end_value) and typed
        try:
            state = root.evaluate(
                """() => {
                    const q = (s) => document.querySelector(s);
                    return {
                        beginVisible: q('#datepicker1-beginDate') && q('#datepicker1-beginDate').value,
                        endVisible: q('#datepicker1-endDate') && q('#datepicker1-endDate').value,
                        beginHidden: q("input[name='Begin_date']") && q("input[name='Begin_date']").value,
                        endHidden: q("input[name='End_date']") && q("input[name='End_date']").value,
                    };
                }"""
            )
        except Exception:
            state = {}
        ctx.log(f"日期设置(键盘输入={typed}): {state}")

        # 键盘输入没生效时退回 JS 直写
        if not typed or state.get("beginHidden") != start_value or state.get("endHidden") != end_value:
            try:
                fallback = root.evaluate(self.JS_SET_DATE_RANGE, [start_value, end_value])
                ctx.log(f"日期改用 JS 写入: {fallback}")
            except Exception:
                pass
        if not (ui.click_js(root, self.sel("query_button"), timeout=2000) or ui.click(root, self.sel("query_button"), timeout=5000)):
            return False
        # 查询是整页刷新，3 秒往往还没加载完，会误读成「没有符合条件的结果」
        deadline = time.time() + float(self.option("query_timeout", 12))
        while time.time() < deadline:
            try:
                if ui.count_rows(root, self.sel("data_rows")) > 0:
                    break
                if not flow.has_data(root, self):
                    break
            except Exception:
                pass
            page.wait_for_timeout(500)
        return True

    # ---------- 采集 ----------

    def _dump_download_state(self, ctx: RunContext, page, root) -> None:
        """下载面板找不到时保留现场：截图 + 各帧可见按钮，便于一次定位。"""
        try:
            shot = ctx.work_dir / "download_fail.png"
            page.screenshot(path=str(shot))
            ctx.log(f"下载失败现场截图: {shot}", level=30)
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"截图失败: {exc}", level=30)
        probe = """() => {
            const out = [];
            for (const el of document.querySelectorAll('button,a,span,div,li,label')) {
                const t = (el.textContent || '').trim();
                if (!t || t.length > 20) continue;
                if (!/下载|导出|EXCEL|Excel|PDF|确定|取消|提示/.test(t)) continue;
                const r = el.getBoundingClientRect();
                const cs = getComputedStyle(el);
                out.push({
                    tag: el.tagName, text: t, id: el.id || '',
                    cls: (el.className || '').toString().slice(0, 35),
                    visible: !(r.width === 0 && r.height === 0) && cs.display !== 'none' && cs.visibility !== 'hidden',
                });
            }
            return out.slice(0, 40);
        }"""
        for index, frame in enumerate(page.frames):
            try:
                items = frame.evaluate(probe)
            except Exception:
                continue
            if items:
                ctx.log(f"frame[{index}] {frame.url[:70]} 按钮: {items[:12]}", level=30)
        try:
            text = root.evaluate("() => (document.body.innerText || '').replace(/\\u00a0/g, ' ').slice(0, 300).replace(/\\n/g, '|')")
            ctx.log(f"业务页文本: {text}", level=30)
        except Exception:
            pass

    def _click_any_frame(self, page, selectors: list[str], timeout: int = 3000) -> bool:
        """下载面板可能出现在新的 iframe 甚至新窗口里，逐个页面/帧尝试点击。"""
        pages = [page]
        try:
            for other in page.context.pages:
                if other is not page:
                    pages.append(other)
        except Exception:
            pass
        for target in reversed(pages):
            for frame in reversed(target.frames):
                locator = ui.find(frame, selectors, timeout=0)
                if locator is None:
                    continue
                try:
                    locator.click(timeout=timeout)
                    return True
                except Exception:
                    try:
                        locator.evaluate("el => el.click()")
                        return True
                    except Exception:
                        continue
        return False

    DOWNLOAD_FUNCS = {"statement": "detailDownXlsx", "receipt": ""}

    def _confirm_download_settings(self, ctx: RunContext, page) -> bool:
        """回单 PDF 会弹「下载设置」：文件过大走异步生成，点确定即提交。"""
        for selector in ("button:text-is('确定')", "button:has-text('确定')"):
            if self._click_any_frame(page, [selector], timeout=2000):
                ctx.log("已点击「下载设置」的确定，回单文件开始生成")
                return True
        return False

    def _open_async_download(self, ctx: RunContext, page) -> bool:
        """进入 客户服务 → 文件异步下载 取件页面。"""
        page = self._active_page(page)
        if not self._click_menu(page, self.sel("async_nav")):
            ctx.log("未找到「客户服务」导航", level=30)
            return False
        page.wait_for_timeout(1500)
        if not self._click_menu(page, self.sel("async_menu")):
            ctx.log("未找到「文件异步下载」菜单", level=30)
            return False
        page.wait_for_timeout(5000)
        return True

    def _wait_async_file(self, ctx: RunContext, page, timeout: float | None = None):
        """轮询异步下载列表，出现可下载链接就取件；超时返回 None。"""
        limit = float(timeout or self.option("async_wait", 120))
        before = flow.snapshot_downloads(ctx)
        deadline = time.time() + limit
        logged = False
        while time.time() < deadline:
            page = self._active_page(page)
            for frame in reversed(page.frames):
                try:
                    links = frame.locator("a:has-text('下载'), button:has-text('下载')")
                    count = links.count()
                    if count == 0:
                        continue
                    if not logged:
                        text = frame.evaluate(
                            "() => (document.body.innerText || '').replace(/\\u00a0/g, ' ').slice(0, 300).replace(/\\n/g, '|')"
                        )
                        ctx.log(f"异步下载页: {text}")
                        logged = True
                    link = links.first
                    if not link.is_visible():
                        continue
                    link.click(timeout=2000)
                    found = flow.wait_download(ctx, before, timeout=25)
                    if found is not None:
                        return found
                except Exception:
                    continue
            page.wait_for_timeout(3000)
        return None

    def _invoke_download_option(self, page, kind: str) -> bool:
        """下载按钮直接绑了 detailDownXlsx() 这类函数，面板也可能还没渲染出来，
        所以先在任何帧里找函数直接调用，找不到再退回按按钮点击。"""
        fn = self.DOWNLOAD_FUNCS.get(kind, "")
        if fn:
            for frame in page.frames:
                try:
                    if frame.evaluate(f"() => typeof window['{fn}'] === 'function'"):
                        frame.evaluate(f"() => window['{fn}']()")
                        return True
                except Exception:
                    continue
        option_key = "excel_option" if kind == "statement" else "pdf_option"
        return self._click_any_frame(page, self.sel(option_key), timeout=1000)

    def _open_download_option(self, ctx: RunContext, page, root, kind: str) -> bool:
        """点「下载」并等待格式面板出现。面板没弹出说明点击落在按钮事件绑定之前，
        重新点一次再找（回单/流水共用）。"""
        page.wait_for_timeout(1500)
        deadline = time.time() + float(self.option("download_panel_timeout", 8))
        while time.time() < deadline:
            if self._invoke_download_option(page, kind):
                return True
            ui.click(root, self.sel("download_button"), timeout=2000)
            page.wait_for_timeout(800)
        return False

    def _download(self, ctx: RunContext, page, root, kind: str, account: str, retries: int = 1, validator=None) -> CollectResult:
        timeout = int(self.option("download_timeout", 60))
        before = flow.snapshot_downloads(ctx)
        started = time.time()

        option_key = "excel_option" if kind == "statement" else "pdf_option"
        if not self._open_download_option(ctx, page, root, kind):
            ctx.log(
                f"未找到下载格式选项: {self.sel(option_key)} "
                f"页面数={len(page.context.pages)} 帧={[f.url[:70] for f in page.frames]}",
                level=30,
            )
            self._dump_download_state(ctx, page, root)
        page.wait_for_timeout(2000)
        # 下载时工行会再次要求 U盾密码。绝不能盲目按回车（等于提交空密码，
        # 会消耗 U盾剩余重试次数甚至锁 Key），出现弹窗就交给认证流程处理。
        if self._popup_open():
            ctx.log("下载时出现 U盾弹窗，转交认证处理")
            auth_ok, _ = self._handle_auth_loop(ctx, page, timeout=int(self.option("auth_timeout", 240)))
            if not auth_ok:
                return CollectResult(error_code=ErrorCode.LOGIN_FAILED, message="下载确认时 U盾认证失败")
        else:
            flow.confirm_dialog(page, self, timeout=3000, native_fallback=False)

        found = flow.wait_download(ctx, before, timeout=timeout)
        if found is None:
            found = flow.latest_after(ctx, started)
        if found is None:
            return CollectResult(error_code=ErrorCode.DOWNLOAD_FAILED, message="等待下载超时")
        if found.suffix.lower() in HTML_SUFFIXES:
            ctx.log(f"下载到 HTML 页面而非文件: {found.name}")
            found.unlink(missing_ok=True)
            if retries > 0:
                page.wait_for_timeout(3000)
                return self._download(ctx, page, root, kind, account, retries=retries - 1)
            return CollectResult(error_code=ErrorCode.DOWNLOAD_FAILED, message="下载内容为 HTML 页面")

        if validator is not None and not validator(found):
            ctx.log("下载到的文件只有表头，按查询结果为空处理")
            found.unlink(missing_ok=True)
            return CollectResult(no_data=True)

        archived = archive_file(
            found,
            ctx.archive_root / ("statements" if kind == "statement" else "receipts"),
            self._prefix(kind),
            account_suffix=account,
            archive_by_month=bool(self.option("archive_by_month", True)),
        )
        ctx.log(f"{'流水' if kind == 'statement' else '回单'}已归档: {archived}")
        return CollectResult(files=[archived])

    # 流水导出表的表头关键字：查询为空时工行照样下载一个只有标记行和表头的表
    STATEMENT_HEADER_KEYS = ("交易时间", "摘要")

    def _statement_file_has_rows(self, path) -> bool:
        """表头之后没有任何数据行，就说明这次查询是空的。"""
        try:
            from openpyxl import load_workbook

            # 下载下来的是无后缀的 GUID 文件，openpyxl 会按扩展名拒绝，改用流读取
            with open(path, "rb") as handle:
                workbook = load_workbook(io.BytesIO(handle.read()), data_only=True)
            rows = [
                row
                for row in workbook.active.iter_rows(values_only=True)
                if any(cell not in (None, "") for cell in row)
            ]
            workbook.close()
        except Exception:  # noqa: BLE001
            return True  # 读不出来的格式按有数据处理，别把正常结果吞掉
        for index, row in enumerate(rows):
            cells = {str(cell) for cell in row if cell is not None}
            if all(key in cells for key in self.STATEMENT_HEADER_KEYS):
                return len(rows) > index + 1
        return True

    def collect_statements(self, ctx: RunContext, session: BrowserSession, account: str, start: str, end: str) -> CollectResult:
        page = session.page
        opened = self._open_menu(ctx, page, "statement")
        if opened is None:
            return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="未能进入明细查询页面")
        page, root = opened
        if not self._query(ctx, root, page, start, end):
            return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="设置日期或点击查询失败")
        if not flow.has_data(root, self):
            ctx.log("查询结果为空")
            return CollectResult(no_data=True)
        return self._download(
            ctx, page, root, "statement", account, validator=self._statement_file_has_rows
        )

    @staticmethod
    def receipt_date_range(start: str, end: str, today: dt.date | None = None) -> tuple[str, str] | None:
        """工行强制限制：不能查询/下载当天回单，结束日期最多到昨天。
        调整后区间无效（起止都在今天）时返回 None。"""
        today = today or dt.date.today()
        start_date = dt.date.fromisoformat(start)
        end_date = min(dt.date.fromisoformat(end), today - dt.timedelta(days=1))
        if start_date > end_date:
            return None
        return start_date.isoformat(), end_date.isoformat()

    def collect_receipts(self, ctx: RunContext, session: BrowserSession, account: str, start: str, end: str) -> CollectResult:
        page = session.page
        limited = self.receipt_date_range(start, end)
        if limited is None:
            ctx.log("回单查询区间不含可下载日期（当天回单不可下载）")
            return CollectResult(no_data=True)
        if limited[1] != end:
            ctx.log(f"回单结束日期从 {end} 调整为 {limited[1]}（当天回单不可下载）")
        start, end = limited

        # 回单和流水在同一个「明细查询」页面，下载面板里分「下载明细」「下载回单信息」两段
        opened = self._open_menu(ctx, page, "statement")
        if opened is None:
            return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="未能进入明细查询页面")
        page, root = opened
        if not self._query(ctx, root, page, start, end):
            return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="设置日期或点击查询失败")
        if not flow.has_data(root, self):
            ctx.log("回单查询结果为空")
            return CollectResult(no_data=True)

        # 回单 PDF 是异步生成：下载 → 回单PDF → 下载设置确定 → 文件异步下载取件
        if not self._open_download_option(ctx, page, root, "receipt"):
            self._dump_download_state(ctx, page, root)
            return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="未找到回单下载按钮")
        page.wait_for_timeout(2500)
        if not self._confirm_download_settings(ctx, page):
            return CollectResult(error_code=ErrorCode.DOWNLOAD_FAILED, message="未出现下载设置弹窗")
        if not self._open_async_download(ctx, page):
            return CollectResult(error_code=ErrorCode.SELECTOR_NOT_FOUND, message="未进入文件异步下载")

        found = self._wait_async_file(ctx, page)
        if found is None:
            ctx.log(f"回单文件 {self.option('async_wait', 120)} 秒内未生成，本次跳过", level=30)
            return CollectResult(no_data=True, message="回单文件未在等待时间内生成")
        archived = archive_file(
            found,
            ctx.archive_root / "receipts",
            self._prefix("receipt"),
            account_suffix=account,
            archive_by_month=bool(self.option("archive_by_month", True)),
        )
        ctx.log(f"回单已归档: {archived}")
        return CollectResult(files=[archived])
