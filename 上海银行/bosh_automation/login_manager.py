# -*- coding: utf-8 -*-
"""
登录流程 - 上海银行适配版

页面：https://ibank.bosc.cn:6074/eib/?#/login
流程：点「验盾登录」→ 填登录密码和 E盾密码 → 点「登录」。

登录密码框是安全输入控件（#_ocx_password），键盘输入统一走 AiKeyboard。
导航时只需要处理证书选择弹窗；E盾密码在页面上输入，不会弹原生 PIN 窗口。
"""
import time

from logger import setup_logger
from common.keyboard_helper import input_with_aikeyboard, send_with_aikeyboard

logger = setup_logger()

# ===== 登录页元素（来自实际页面） =====
# 「验盾登录」页签；页面默认可能停在验盾登录，点击一次也没有副作用
LOGIN_TAB_SELECTOR = "li.account-tab:has-text('验盾登录')"
# 登录密码（安全输入控件）
PASSWORD_SELECTOR = "#_ocx_password"
# E盾密码（U 盾 PIN）
PIN_SELECTOR = "#e_pwd_input"
# 登录按钮
LOGIN_BUTTON_SELECTOR = (
    "body > div.wrapper > div.container > div.login-card > div > div.account-login "
    "> div:nth-child(3) > form > div.btn-box > button"
)
# =====================================

CERT_TITLES = ("选择证书", "客户端证书", "证书")


def _foreground_title() -> str:
    """当前前台窗口标题，用来确认 AiKeyboard 的按键真的落在采集浏览器上。"""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value
    except Exception:
        return ""


class LoginManagerMixin:
    """登录流程 Mixin，挂在 BOSCBrowser 上。"""

    def _find_hwnd(self, titles):
        try:
            from common.windows_ui import find_hwnd
        except ImportError:
            return None
        return find_hwnd(titles)

    def _handle_certificate_dialog(self) -> bool:
        """处理证书选择弹窗：默认选中第一张证书，回车确认。"""
        hwnd = self._find_hwnd(CERT_TITLES)
        if not hwnd:
            return False
        logger.info("检测到证书选择弹窗，回车确认默认证书...")
        send_with_aikeyboard("enter")
        time.sleep(1)
        return True

    def _handle_auth_popups(self, timeout: float = 60.0) -> None:
        """导航期间处理证书选择弹窗。"""
        deadline = time.time() + timeout
        last = time.time()

        while time.time() < deadline:
            if self._handle_certificate_dialog():
                last = time.time()
                continue
            if time.time() - last > 8:
                break
            time.sleep(0.3)

        logger.info("认证弹窗处理结束")

    def _browser_hwnd(self) -> int:
        """找到采集浏览器主窗口句柄（按 user_data_dir 认进程，避免认错）。"""
        if getattr(self, "_hwnd", 0):
            return self._hwnd
        try:
            import subprocess

            marker = str(self.user_data_dir or "").replace("'", "''")
            if not marker:
                return 0
            script = (
                "$p = Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                f"Where-Object {{ $_.CommandLine -and $_.CommandLine.Contains('{marker}') }}; "
                "foreach ($x in $p) { $proc = Get-Process -Id $x.ProcessId -ErrorAction SilentlyContinue; "
                "if ($proc -and $proc.MainWindowHandle -ne 0) { $proc.MainWindowHandle } }"
            )
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                timeout=15,
            ).stdout
            handles = [int(part) for part in out.split() if part.strip().isdigit()]
            if handles:
                self._hwnd = handles[0]
        except Exception as e:
            logger.debug(f"查找浏览器窗口失败: {e}")
        return getattr(self, "_hwnd", 0)

    def _force_foreground(self, hwnd: int) -> bool:
        """把指定窗口强制切到前台；普通 SetForegroundWindow 常被系统拒绝。"""
        import ctypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not hwnd:
            return False

        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        if user32.GetForegroundWindow() == hwnd:
            return True

        current = kernel32.GetCurrentThreadId()
        foreground = user32.GetForegroundWindow()
        foreground_tid = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
        attached = False
        if foreground_tid and foreground_tid != current:
            attached = bool(user32.AttachThreadInput(current, foreground_tid, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            # 不要 SetFocus(窗口句柄)：那会把键盘焦点交给 Chrome 的默认控件（地址栏），
            # 后续按键会打进地址栏。焦点交给页面里的点击操作去设置。
        finally:
            if attached:
                user32.AttachThreadInput(current, foreground_tid, False)
        time.sleep(0.3)
        return user32.GetForegroundWindow() == hwnd

    def _focus_browser(self) -> bool:
        """把采集浏览器窗口强制切到前台，返回是否真的成功。"""
        hwnd = self._browser_hwnd()
        if self._force_foreground(hwnd):
            return True
        try:
            self.page.bring_to_front()
        except Exception:
            pass
        return self._foreground_is_browser()

    def _foreground_is_browser(self) -> bool:
        hwnd = self._browser_hwnd()
        if not hwnd:
            return False
        import ctypes

        return ctypes.windll.user32.GetForegroundWindow() == hwnd

    def _native_focus_is_page(self) -> bool:
        """检查系统键盘焦点是否落在页面渲染控件上（而不是地址栏等浏览器界面）。"""
        import ctypes
        import ctypes.wintypes as wt

        hwnd = self._browser_hwnd()
        if not hwnd:
            return False

        class GUITHREADINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wt.DWORD),
                ("flags", wt.DWORD),
                ("hwndActive", wt.HWND),
                ("hwndFocus", wt.HWND),
                ("hwndCapture", wt.HWND),
                ("hwndMenuOwner", wt.HWND),
                ("hwndMoveSize", wt.HWND),
                ("hwndCaret", wt.HWND),
                ("rcCaret", wt.RECT),
            ]

        user32 = ctypes.windll.user32
        info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
        tid = user32.GetWindowThreadProcessId(hwnd, None)
        if not user32.GetGUIThreadInfo(tid, ctypes.byref(info)) or not info.hwndFocus:
            return False
        name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(info.hwndFocus, name, 256)
        self._last_focus_class = name.value
        # Chrome 有时把焦点报在顶层窗口（Chrome_WidgetWin_1），不一定是渲染子窗口
        return "RenderWidgetHost" in name.value or "Chrome_WidgetWin" in name.value

    def _hard_click(self, field) -> bool:
        """用系统鼠标事件点击元素中心，把系统键盘焦点真正交给页面。

        Playwright 的点击是合成事件，只改页面内焦点，地址栏仍持有系统焦点。
        """
        import ctypes

        try:
            box = field.bounding_box()
            metrics = self.page.evaluate(
                """() => ({
                    sx: window.screenX, sy: window.screenY,
                    ow: window.outerWidth, iw: window.innerWidth,
                    oh: window.outerHeight, ih: window.innerHeight,
                })"""
            )
        except Exception:
            return False
        if not box:
            return False

        left = metrics["sx"] + (metrics["ow"] - metrics["iw"]) / 2
        top = metrics["sy"] + (metrics["oh"] - metrics["ih"])
        x = int(left + box["x"] + box["width"] / 2)
        y = int(top + box["y"] + box["height"] / 2)

        user32 = ctypes.windll.user32
        user32.SetCursorPos(x, y)
        time.sleep(0.2)
        user32.mouse_event(0x0002, 0, 0, 0, 0)  # 左键按下
        user32.mouse_event(0x0004, 0, 0, 0, 0)  # 左键抬起
        time.sleep(0.3)
        return True

    def _focus_input(self, field, selector: str, label: str) -> bool:
        """点击输入框并确认焦点真的进去。

        校验 DOM 焦点、页面焦点和系统原生焦点；系统焦点必须落在页面渲染控件上。
        """
        for attempt in range(6):
            self._focus_browser()
            if not self._hard_click(field):
                field.click()
            time.sleep(0.4)
            if not self._foreground_is_browser():
                logger.info(f"第 {attempt + 1} 次点击后浏览器仍不在前台，重试...")
                time.sleep(0.5)
                continue
            state = self.page.evaluate(
                """(sel) => {
                    const el = document.querySelector(sel);
                    const active = document.activeElement;
                    return {
                        active: active ? (active.id || active.tagName) : '',
                        inside: !!(el && active && (el === active || el.contains(active))),
                        page_focus: document.hasFocus(),
                    };
                }""",
                selector,
            )
            native_ok = self._native_focus_is_page()
            if state.get("inside") and state.get("page_focus") and native_ok:
                return True
            logger.info(
                f"{label} 第 {attempt + 1} 次点击后焦点在 {state.get('active')!r}，"
                f"页面聚焦={state.get('page_focus')}，原生页面焦点={native_ok}"
                f"（焦点窗口类={getattr(self, '_last_focus_class', '')}），重试..."
            )
            time.sleep(0.5)
        return False

    def _fill_security_field(self, selector: str, value: str, label: str) -> None:
        """安全控件输入框：点击聚焦后用 AiKeyboard 输入（不允许用键盘注入）。"""
        field = self.page.locator(selector).first
        field.wait_for(state="visible", timeout=10000)
        if not self._focus_input(field, selector, label):
            logger.warning(f"{label} 输入框没能获得焦点，仍尝试输入")
        if not self._native_focus_is_page():
            # AiKeyboard 按键跟系统焦点走，原生焦点不在页面就绝不能输入
            raise RuntimeError(f"{label} 输入前系统焦点不在采集浏览器页面，已停止输入以免密码打到地址栏或其它窗口")
        time.sleep(0.2)

        # 清空可能残留的内容（安全控件读不到长度，多按几次退格）
        for _ in range(len(value) + 6):
            send_with_aikeyboard("backspace")

        logger.info(f"使用 AiKeyboard 输入{label}（{len(value)} 位），前台窗口: {_foreground_title()}")
        if not input_with_aikeyboard(value):
            raise RuntimeError(f"{label} 输入失败：AiKeyboard 不可用")
        time.sleep(0.3)

    def _click_login_tab(self) -> None:
        try:
            tab = self.page.locator(LOGIN_TAB_SELECTOR).first
            if tab.is_visible():
                tab.click()
                time.sleep(0.5)
                logger.info("已切换到「验盾登录」")
        except Exception as e:
            logger.debug(f"切换「验盾登录」页签跳过: {e}")

    def _portal_page(self):
        """在浏览器上下文里找已登录的页面（URL 离开登录路由，或页头出现「退出」）。"""
        try:
            pages = self.context.pages
        except Exception:
            return None

        for page in reversed(pages):
            try:
                url = (page.url or "").lower()
            except Exception:
                continue
            if "about:blank" in url:
                continue
            if "/eib" in url and "login" not in url:
                return page
            try:
                if page.locator("text=退出").first.is_visible(timeout=1000):
                    return page
            except Exception:
                continue
        return None

    def _adopt_portal_page(self, timeout: float = 20.0) -> bool:
        """登录后可能新开标签页，等待并把它接管为主页面。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            page = self._portal_page()
            if page is not None:
                if page is not self.page:
                    logger.info(f"切换到登录后的页面: {page.url}")
                    self.page = page
                    try:
                        page.bring_to_front()
                    except Exception:
                        pass
                return True
            time.sleep(1)
        return False

    def _check_login_success(self) -> bool:
        """判断是否已经登录；登录后页面可能换了标签页。"""
        ok = self._adopt_portal_page(timeout=3)
        if not ok:
            try:
                urls = [page.url for page in self.context.pages]
            except Exception:
                urls = []
            logger.info(f"未检测到已登录页面，当前页面: {urls}")
        return ok

    def login(self) -> bool:
        """执行登录：验盾登录 → 登录密码 → E盾密码 → 登录。"""
        logger.info("=" * 50)
        logger.info("开始登录流程（上海银行）...")

        credentials = self.credentials
        if credentials is None:
            logger.error("没有可用的登录凭据")
            return False

        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=30000)
            time.sleep(2)

            self._click_login_tab()

            if credentials.password:
                self._fill_security_field(PASSWORD_SELECTOR, credentials.password, "登录密码")
            if credentials.pin:
                self._fill_security_field(PIN_SELECTOR, credentials.pin, "E盾密码")

            # 登录后 U 盾中间件可能再弹一次证书选择
            logger.info("点击登录...")
            self.page.locator(LOGIN_BUTTON_SELECTOR).first.click()
            self._handle_auth_popups(timeout=60)
            self._adopt_portal_page(timeout=20)

            time.sleep(3)
            success = self._check_login_success()
            if not success:
                self._save_debug_screenshot()
            return success
        except Exception as e:
            logger.error(f"登录流程出错: {e}")
            self._save_debug_screenshot()
            return False

    def _save_debug_screenshot(self, name: str = "login_failed") -> None:
        """登录失败时留一张截图，方便判断卡在登录页还是别的页面。"""
        try:
            import datetime as dt
            from pathlib import Path

            path = Path("debug") / f"{name}_{dt.datetime.now():%Y%m%d_%H%M%S}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(path))
            logger.info(f"已保存失败截图: {path.resolve()}")
        except Exception as e:
            logger.debug(f"保存截图失败: {e}")
