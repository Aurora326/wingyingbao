# -*- coding: utf-8 -*-
"""
浏览器模块 - 工商银行适配
"""
import os
import sys
import time
import threading
import subprocess as _sp
from pathlib import Path
from typing import Optional
from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext
from contextlib import contextmanager

from logger import setup_logger
from config_loader import BrowserConfig, CredentialsConfig

# 通用弹窗检测在仓库根的 common 里（新老采集程序共用一份）
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.windows_ui import (  # noqa: E402
    find_hwnd,
    find_chrome_dialog,
    click_in_dialog,
    double_click_in_dialog,
    click_dialog_button,
    click_chrome_dialog_button,
    fast_click_button,
    read_dialog_text,
    window_process_name,
    list_windows,
)

# 工行中间件 MWICBCUKeyUI.exe 的密码框标题有「输入密码」也有「操作提示」
PIN_TITLES = ("输入密码", "校验U盾密码", "U盾密码", "操作提示")
CERT_TITLES = ("选择证书", "证书")
# 「用户提示」是密码错误后的「是否继续重试」询问框，必须终止而不是重试
ERROR_TITLES = ("用户提示",)

LOGIN_PAGE_WAIT = 2.5
AUTH_TIMEOUT = 240
AUTH_IDLE_TIMEOUT = 20
CHROME_SCAN_INTERVAL = 1.0
PIN_CLOSE_TIMEOUT = 12

LOGIN_BUTTON_SELECTORS = [
    "div.standard_login_con > button",
    "button:has-text('U盾登录')",
    "text=U盾登录",
]
LOGGED_IN_SELECTORS = ["text=退出", ".logout", "text=工作台"]

logger = setup_logger()


class ICBCBrowser:
    """工商银行浏览器自动化类"""

    def __init__(self, config: BrowserConfig, credentials: CredentialsConfig):
        self.config = config
        self.credentials = credentials
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.extension_dir: Optional[str] = None
        self.extension_loaded: bool = False
        self.extension_installed_in_profile: bool = False

    def _find_chrome_path(self) -> Optional[str]:
        """查找 Chrome/Edge 路径"""
        if self.config.chrome_path and Path(self.config.chrome_path).exists():
            return self.config.chrome_path

        paths = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        ]
        for p in paths:
            if Path(p).exists():
                return p
        return None

    def _get_extension_path(self) -> str:
        """获取扩展路径（工行安全控件，后续按实际控件调整）"""
        current_file = Path(__file__).resolve()
        project_root = current_file.parent.parent

        extension_dir = project_root / "crx_extracted"
        if not extension_dir.exists():
            # TODO: 替换为工行实际安全控件文件名
            crx_file = project_root / "ICBC_Security.crx"
            if crx_file.exists():
                logger.info("正在解压扩展...")
                self._extract_crx(str(crx_file), str(extension_dir))
            else:
                configured = Path(self.config.extension_path).expanduser().resolve()
                if configured.exists() and configured.suffix.lower() == ".crx":
                    logger.info("正在解压配置路径中的扩展...")
                    self._extract_crx(str(configured), str(extension_dir))

        if extension_dir.exists():
            return str(extension_dir.resolve())

        return str(Path(self.config.extension_path).resolve())

    def _extract_crx(self, crx_path: str, dest_dir: str):
        """解压 crx 文件"""
        import zipfile
        import struct
        import io

        data = Path(crx_path).read_bytes()
        if data[:4] == b"Cr24":
            version = struct.unpack('<I', data[4:8])[0]
            if version == 2:
                header_len = struct.unpack('<I', data[8:12])[0]
                zip_start = 12 + header_len
            else:
                zip_start = 14
            zip_data = data[zip_start:]
        else:
            zip_data = data

        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            zf.extractall(dest_dir)

        logger.info(f"扩展已解压到: {dest_dir}")

    def _get_extension_id(self) -> Optional[str]:
        """从扩展元数据中读取扩展 ID"""
        try:
            project_root = Path(__file__).resolve().parent.parent
            meta_file = project_root / "crx_extracted" / "_metadata" / "verified_contents.json"
            if not meta_file.exists():
                return None
            import json
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                payload = data[0].get("signed_content", {}).get("payload")
                if payload:
                    import base64
                    decoded = base64.b64decode(payload + "===").decode("utf-8")
                    return json.loads(decoded).get("item_id")
            return None
        except Exception:
            return None

    def _is_extension_installed_in_profile(self, user_data_dir: Path) -> bool:
        """检查扩展是否已安装到用户数据目录"""
        ext_id = self._get_extension_id()
        if not ext_id:
            return False
        ext_root = user_data_dir / "Default" / "Extensions" / ext_id
        if not ext_root.exists():
            return False
        return any(child.is_dir() for child in ext_root.iterdir())

    def start(self) -> Page:
        """启动浏览器并返回页面对象"""
        logger.info("正在启动浏览器...")

        self.playwright = sync_playwright().start()

        chrome_path = self._find_chrome_path()
        if chrome_path:
            logger.info(f"使用 Chrome: {chrome_path}")

        base_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
        user_data_dir = base_dir / "chrome_user_data"
        user_data_dir.mkdir(exist_ok=True)
        self.user_data_dir = user_data_dir
        logger.info(f"用户数据目录: {user_data_dir}")

        installed_in_profile = self._is_extension_installed_in_profile(user_data_dir)
        self.extension_installed_in_profile = installed_in_profile
        if installed_in_profile:
            self.extension_dir = None
            logger.info("扩展已安装到用户数据目录，将直接加载")
        else:
            extension_dir = self._get_extension_path()
            if extension_dir and Path(extension_dir).exists() and Path(extension_dir).is_dir():
                self.extension_dir = extension_dir
            else:
                self.extension_dir = None

        window_width = int(getattr(self.config, "window_width", 1750) or 1750)
        window_height = int(getattr(self.config, "window_height", 900) or 900)

        launch_options = {
            "headless": self.config.headless,
            # 关掉视口模拟，--window-size 才是整个窗口的实际尺寸
            "no_viewport": True,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                f"--window-size={window_width},{window_height}",
                "--kiosk-printing",
                "--disable-print-preview",
                "--disable-download-notification",
                "--disable-features=DownloadBubble,DownloadBubbleV2",
                "--safebrowsing-disable-download-protection",
                "--safebrowsing-disable-extension-blacklist",
                "--enable-automation",
                "--allow-file-access",
                "--allow-file-access-from-files",
                "--reduce-security-for-testing",
                "--ignore-certificate-errors",
                # 尝试自动选择U盾证书，失败时由 _handle_certificate 用回车兜底
                "--auto-select-certificate-for-urls=https://corporbank.icbc.com.cn,https://corporbank-simp.icbc.com.cn",
            ],
        }
        if chrome_path:
            launch_options["executable_path"] = chrome_path

        if installed_in_profile:
            launch_options["ignore_default_args"] = [
                "--enable-automation",
                "--disable-extensions",
                "--disable-component-extensions-with-background-pages",
                "--disable-component-update",
            ]

        if self.extension_dir:
            if self.config.headless:
                logger.warning("headless=true 时不支持安全控件扩展，请设置 headless: false")
            else:
                launch_options["args"].extend([
                    f"--disable-extensions-except={self.extension_dir}",
                    f"--load-extension={self.extension_dir}",
                ])
                self.extension_loaded = True
                logger.info(f"加载扩展: {self.extension_dir}")

        download_path = base_dir / "downloads"
        download_path.mkdir(exist_ok=True)
        launch_options["downloads_path"] = str(download_path.resolve())
        logger.info(f"浏览器下载路径: {download_path}")

        self.context = self.playwright.chromium.launch_persistent_context(
            str(user_data_dir),
            **launch_options
        )

        pages = self.context.pages
        self.page = pages[0] if pages else self.context.new_page()

        try:
            self.page.evaluate(
                "(size) => { window.moveTo(0, 0); window.resizeTo(size[0], size[1]); }",
                [window_width, window_height],
            )
            logger.info(f"浏览器窗口尺寸: {window_width}x{window_height}")
        except Exception as e:
            logger.debug(f"窗口最大化设置失败: {e}")

        self.page.set_default_timeout(self.config.page_timeout * 1000)
        logger.info("浏览器已启动")

        self._check_extension_installed()
        return self.page

    def _check_extension_installed(self) -> bool:
        """检查安全控件扩展是否已安装"""
        try:
            if self.extension_loaded or self.extension_installed_in_profile:
                logger.info("扩展已加载")
                return True
            self.page.goto("chrome://extensions/", timeout=10000)
            time.sleep(2)
            page_content = self.page.content()
            # TODO: 按工行安全控件实际名称调整
            if "ICBC" in page_content or "工行" in page_content:
                logger.info("检测到安全控件扩展已安装")
                return True
            logger.warning("未检测到安全控件扩展")
            return False
        except Exception as e:
            logger.warning(f"检查扩展状态时出错: {e}")
            return False

    def navigate_to_login(self):
        """导航到登录页。

        用只发不等的导航：证书弹窗会在导航途中弹出，goto 会一直挂着不返回。
        """
        logger.info(f"正在访问: {self.config.login_url}")
        self._goto_nowait(self.config.login_url)
        self._wait_popup(LOGIN_PAGE_WAIT)

        try:
            if not self._popup_open():
                self.page.mouse.click(200, 200)
                logger.info("已点击页面空白区域，确保焦点在页面上")
                time.sleep(0.5)
                self.page.keyboard.press("Escape")
                time.sleep(0.3)
        except Exception as e:
            logger.debug(f"焦点初始化失败: {e}")

        self._close_announcement_dialog()

    def _close_announcement_dialog(self):
        """关闭公告/通知弹窗"""
        try:
            close_selectors = [
                ".modal.in button.close",
                ".modal button.close",
                ".close-btn",
                "button:has-text('关闭')",
                "a:has-text('关闭')",
            ]
            for selector in close_selectors:
                try:
                    btn = self.page.locator(selector).first
                    if btn.count() > 0 and btn.is_visible(timeout=1000):
                        btn.click(force=True)
                        logger.info(f"已关闭公告弹窗: {selector}")
                        time.sleep(1)
                        return
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"关闭公告弹窗失败: {e}")

    def _get_aikeyboard_path(self) -> Optional[Path]:
        """获取 AiKeyboard.exe 的路径（支持多种运行环境）"""
        if getattr(sys, 'frozen', False):
            exe_dir = Path(sys.executable).parent
        else:
            exe_dir = Path(__file__).parent.parent.parent

        possible_paths = [
            exe_dir / "keyboard" / "AiKeyboard.exe",
            exe_dir / ".." / "keyboard" / "AiKeyboard.exe",
            exe_dir / ".." / ".." / "keyboard" / "AiKeyboard.exe",
            Path.cwd() / "keyboard" / "AiKeyboard.exe",
        ]

        for path in possible_paths:
            path = path.resolve()
            if path.exists():
                logger.info(f"找到 AiKeyboard.exe: {path}")
                return path

        logger.warning(f"AiKeyboard.exe 未找到，尝试过的路径: {[str(p.resolve()) for p in possible_paths]}")
        return None

    def _input_with_aikeyboard(self, text: str) -> bool:
        """使用 AiKeyboard.exe 虚拟键盘输入密码"""
        try:
            import subprocess

            aikeyboard_path = self._get_aikeyboard_path()
            if not aikeyboard_path:
                return False

            logger.info("使用 AiKeyboard 输入密码...")

            # 输入法已在 _force_english_input 里统一处理，这里不再按 Shift，
            # 否则可能反而把英文切回中文。间隔压到 120ms，两次密码之间不能等太久
            time.sleep(0.2)

            result = subprocess.run(
                [str(aikeyboard_path), "input", "-k", text, "-i", "120"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                logger.info("AiKeyboard 输入完成")
                return True

            logger.error(f"AiKeyboard 失败: {result.stderr}")
            return False
        except Exception as e:
            logger.error(f"AiKeyboard 调用失败: {e}")
            return False

    # ---------- 弹窗检测：全程走 Windows 原生接口 ----------
    # U盾证书/密码弹窗一出现，Chrome 渲染进程就进入模态状态，
    # 任何 Playwright 调用都会永久挂起，所以弹窗期间绝不碰页面。

    def _popup_state(self):
        """返回 (密码弹窗, 证书弹窗)。原生窗口毫秒级枚举，Chrome 内置证书弹窗扫 UIA 较慢，按间隔节流。"""
        if find_hwnd(PIN_TITLES) is not None:
            return True, False
        if find_hwnd(CERT_TITLES) is not None:
            return False, True
        now = time.time()
        if now - getattr(self, "_last_chrome_scan", 0.0) < CHROME_SCAN_INTERVAL:
            return False, getattr(self, "_last_chrome_result", False)
        self._last_chrome_scan = now
        found = find_chrome_dialog(CERT_TITLES) is not None
        self._last_chrome_result = found
        return False, found

    def _popup_open(self) -> bool:
        pin_visible, cert_visible = self._popup_state()
        return pin_visible or cert_visible

    def _logged_in(self) -> bool:
        # 有原生弹窗时渲染进程是卡住的，任何 DOM 查询都会挂起。
        # 注意：不能用 URL 判断——登录后的首页地址和登录页是同一个 index.html
        if self._popup_open() or find_hwnd(ERROR_TITLES) is not None:
            return False
        try:
            for selector in LOGGED_IN_SELECTORS:
                # count() 在页面跳转期间会一直阻塞，wait_for_selector 有明确超时
                try:
                    self.page.wait_for_selector(selector, timeout=1200)
                    return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    @contextmanager
    def _short_timeout(self, ms: int = 2000):
        """页面加载/模态期间 Playwright 会按默认 30s 反复重试，一次点击就白等 30 秒。"""
        try:
            self.page.set_default_timeout(ms)
        except Exception:
            pass
        try:
            yield
        finally:
            try:
                self.page.set_default_timeout(int(getattr(self.config, "page_timeout", 30)) * 1000)
            except Exception:
                pass

    def _goto_nowait(self, url: str) -> None:
        """发起导航但不等待完成。证书弹窗会把渲染进程拖入模态，goto 永远不返回。"""
        try:
            with self._short_timeout():
                self.page.evaluate("(u) => { window.location.href = u; }", url)
            logger.info("登录页导航已发起")
        except Exception as exc:
            logger.info(f"登录页导航已发起（上下文切换）: {type(exc).__name__}")

    def _wait_popup(self, timeout: float):
        deadline = time.time() + timeout
        while True:
            pin_visible, cert_visible = self._popup_state()
            if pin_visible or cert_visible:
                return pin_visible, cert_visible
            if time.time() >= deadline:
                return False, False
            time.sleep(0.1)

    def _handle_error_dialog(self) -> bool:
        """识别并关闭 U盾中间件的系统提示，绝不重试，避免继续消耗剩余次数。"""
        hwnd = find_hwnd(ERROR_TITLES)
        if hwnd is None:
            return False
        message = read_dialog_text(ERROR_TITLES, timeout=0.5)
        process = window_process_name(hwnd).lower()
        if "icbc" not in process and not any(k in message for k in ("U盾", "密码", "重试", "Key")):
            return False
        logger.error(f"检测到系统提示(进程={process or '未知'}): {message.strip()[:100]}")
        clicked = click_dialog_button(ERROR_TITLES, ("否", "取消", "关闭"), ratio=(0.72, 0.5), timeout=2)
        logger.error(f"已关闭系统提示: {clicked}")
        return True

    def _handle_auth_loop(self, timeout: int = AUTH_TIMEOUT):
        """只用 Windows 原生检测处理证书/密码弹窗，弹窗期间不调用 Playwright。"""
        logger.info(f"开始检测登录弹窗（最长 {timeout}s，空闲 {AUTH_IDLE_TIMEOUT}s 退出）")
        deadline = time.time() + timeout
        handled = 0
        last_progress = time.time()
        last_log = 0.0
        while time.time() < deadline:
            if self._handle_error_dialog():
                logger.error("U盾提示密码有误或已停止重试，终止登录")
                return False, handled
            pin_visible, cert_visible = self._popup_state()
            if pin_visible:
                last_progress = time.time()
                handled += 1
                logger.info(f"检测到第 {handled} 个 U盾密码弹窗")
                closed = self._handle_ukey_password(self.credentials.pin or "")
                # 密码弹窗处理本身可能比空闲超时还久，处理完要重新计时，
                # 否则一提交就立刻判定空闲超时，页面来不及跳转
                last_progress = time.time()
                if not closed and find_hwnd(PIN_TITLES) is not None:
                    logger.error("密码弹窗未关闭，停止自动重试（避免锁 Key）")
                    break
                continue
            if cert_visible:
                self._handle_certificate()
                last_progress = time.time()
                continue
            now = time.time()
            if now - last_progress > AUTH_IDLE_TIMEOUT:
                logger.info(f"{AUTH_IDLE_TIMEOUT}s 内未出现证书/密码弹窗，结束检测")
                break
            if now - last_log >= 5:
                logger.info(f"等待认证弹窗中: cert={cert_visible} pin={pin_visible} 已处理密码={handled}")
                last_log = now
            time.sleep(0.2)
        # 注意：这里不能读 page.url，也不查 DOM——登录后页面还在加载，
        # Playwright 的任何查询都会阻塞到整个页面稳定，实测要 30-60 秒
        if find_hwnd(PIN_TITLES) is not None:
            return False, handled
        if handled and find_hwnd(ERROR_TITLES) is None:
            logger.info("密码已全部提交且无错误提示，按登录成功继续")
            return True, handled
        return self._logged_in(), handled

    def login(self) -> bool:
        """执行登录流程（工行 U盾登录）"""
        logger.info("=" * 50)
        logger.info("开始登录流程（U盾）")
        logger.info(f"PIN: {'已设置' if self.credentials.pin else '未设置'}")

        try:
            # main.py 已经调过 navigate_to_login，这里不再重复导航。
            # 读 page.url 在页面跳转期间会阻塞几十秒，不作为判断依据
            if self._popup_open():
                logger.info("检测到已存在的登录弹窗，直接处理")
            else:
                logger.info("[步骤1] 点击企业网银 U盾登录")
                if not self.click_ukey_login():
                    logger.error("未找到 U盾登录按钮")
                    return False

            logger.info("[步骤2] 等待证书/密码弹窗")
            ok, handled = self._handle_auth_loop(AUTH_TIMEOUT)
            if ok:
                logger.info(f"✓ 登录成功（处理密码弹窗 {handled} 次）")
                self._close_home_ad()
                return True
            logger.warning(f"登录失败（处理密码弹窗 {handled} 次）")
            return False
        except Exception as e:
            logger.error(f"登录异常: {e}")
            return False



    def click_ukey_login(self) -> bool:
        """点击 U盾登录按钮。弹窗已出现时页面已不可交互，直接返回。"""
        if self._popup_open():
            logger.info("U盾弹窗已出现，跳过页面点击")
            return True

        with self._short_timeout():
            for selector in LOGIN_BUTTON_SELECTORS:
                try:
                    # 用带超时的 wait_for_selector，避免 count() 在页面跳转时长时间阻塞
                    self.page.wait_for_selector(selector, state="visible", timeout=1500)
                    self.page.locator(selector).first.evaluate("el => el.click()")
                    logger.info(f"已点击 U盾登录按钮: {selector}")
                    return True
                except Exception as exc:
                    logger.debug(f"点击 {selector} 失败: {exc}")

        if self._popup_open():
            logger.info("点击后 U盾弹窗出现，停止页面操作")
            return True

        try:
            import pyautogui
            pyautogui.FAILSAFE = False
            width, height = pyautogui.size()
            x, y = int(width * 0.70), int(height * 0.46)
            logger.warning(f"未找到按钮，按屏幕比例兜底点击: ({x}, {y})")
            pyautogui.click(x, y)
            time.sleep(0.5)
            return True
        except Exception as exc:
            logger.error(f"兜底点击失败: {exc}")
            return False



    def _wait_for_window(self, keywords, timeout: int = 15):
        """等待指定标题的 Windows 原生窗口出现"""
        try:
            import pygetwindow as gw
        except Exception:
            logger.warning("pygetwindow 未安装，无法检测系统弹窗")
            return None

        end = time.time() + timeout
        while time.time() < end:
            try:
                for win in gw.getAllWindows():
                    title = (win.title or "").strip()
                    if title and any(k in title for k in keywords):
                        return win
            except Exception:
                pass
            time.sleep(0.5)

        return None

    @staticmethod
    def _activate_window(win):
        """激活 Windows 窗口"""
        try:
            import pygetwindow as gw
            if win.isMinimized:
                win.restore()
            win.activate()
            time.sleep(0.5)
        except Exception as e:
            logger.debug(f"激活窗口失败: {e}")

    def _press_enter(self) -> bool:
        """按回车（确认系统弹窗默认按钮）"""
        try:
            import pyautogui
            pyautogui.press("enter")
            return True
        except Exception as e:
            logger.debug(f"pyautogui 回车失败，尝试 AiKeyboard: {e}")

        try:
            import subprocess
            aikeyboard = self._get_aikeyboard_path()
            if aikeyboard:
                subprocess.run([str(aikeyboard), "send", "-k", "enter"],
                               capture_output=True, timeout=10)
                return True
        except Exception as e:
            logger.warning(f"发送回车失败: {e}")
        return False

    def _handle_certificate(self) -> bool:
        """确认证书选择弹窗：双击列表第一行，再点确定。"""
        logger.info("处理证书选择弹窗...")
        if find_hwnd(CERT_TITLES):
            if double_click_in_dialog(CERT_TITLES, 0.57, 0.36, timeout=2):
                logger.info("已双击证书行")
                time.sleep(0.5)
                click_in_dialog(CERT_TITLES, 0.71, 0.87, timeout=1)
            elif click_dialog_button(CERT_TITLES, ("确定", "确认", "OK"), ratio=(0.71, 0.87), timeout=1.5):
                logger.info("已点击证书弹窗的「确定」")
            time.sleep(0.8)
            return True

        if click_chrome_dialog_button(CERT_TITLES, ("确定", "确认", "OK"), timeout=8):
            logger.info("已点击证书弹窗（Chrome 内置）的「确定」")
            time.sleep(0.8)
            return True

        logger.warning(f"未找到证书弹窗；当前窗口: {list_windows(('证书', '密码', 'U盾'))}")
        return False



    def _force_english_input(self, title_keywords) -> bool:
        """把弹窗所在窗口的键盘布局切成英文。

        中文输入法处于激活状态时，PIN 里的字母会被转成汉字，
        AiKeyboard 是按系统焦点盲打的，结果就是输错密码。
        """
        hwnd = find_hwnd(title_keywords)
        if not hwnd:
            return False
        try:
            import ctypes

            user32 = ctypes.windll.user32
            tid = user32.GetWindowThreadProcessId(hwnd, None)
            if (user32.GetKeyboardLayout(tid) & 0xFFFF) in (0x0409, 0x0809):
                return True
            english = user32.LoadKeyboardLayoutW("00000409", 0x00000001)
            user32.PostMessageW(hwnd, 0x0050, 0, english)  # WM_INPUTLANGCHANGEREQUEST
            time.sleep(0.4)
            switched = (user32.GetKeyboardLayout(tid) & 0xFFFF) in (0x0409, 0x0809)
            logger.info(f"切换英文输入: {'成功' if switched else '未生效'}")
            return switched
        except Exception as e:
            logger.warning(f"切换英文输入失败: {e}")
            return False

    def _pin_field_focused(self) -> bool:
        """确认当前键盘焦点真的落在密码弹窗的输入框上。

        AiKeyboard 是往系统前台焦点打的，焦点没进输入框就会把密码打到别处。
        """
        hwnd = find_hwnd(PIN_TITLES)
        if not hwnd:
            return False
        try:
            import ctypes
            from ctypes import wintypes

            class GUITHREADINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("hwndActive", wintypes.HWND),
                    ("hwndFocus", wintypes.HWND),
                    ("hwndCapture", wintypes.HWND),
                    ("hwndMenuOwner", wintypes.HWND),
                    ("hwndMoveSize", wintypes.HWND),
                    ("hwndCaret", wintypes.HWND),
                    ("rcCaret", wintypes.RECT),
                ]

            user32 = ctypes.windll.user32
            info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
            tid = user32.GetWindowThreadProcessId(hwnd, None)
            if not user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
                return False
            focus = info.hwndFocus
            if not focus or not user32.IsChild(hwnd, focus):
                return False
            buffer = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(focus, buffer, 64)
            return buffer.value.lower() == "edit"
        except Exception as e:
            logger.debug(f"检查密码框焦点失败: {e}")
            return False

    def _handle_ukey_password(self, pin: str) -> bool:
        """输入 U盾 PIN。

        工行要求输两次密码，两次之间隔太久连接会断，所以这一步必须快：
        全程走 Win32 定位控件，不用 pywinauto（枚举一次要几十秒）。
        """
        if not pin:
            logger.warning("未配置 PIN，等待人工输入")
            time.sleep(30)
            return False

        # 尽量切英文，切不动也继续：密码框是密码控件，本身不接输入法，
        # 实测 9 位数字字母能原样输入。检测结果只用于日志
        if not self._force_english_input(PIN_TITLES):
            logger.warning("未能确认英文输入法，继续按原样输入")

        # 必须真实点击并把弹窗置前：AiKeyboard 是往系统的前台焦点窗口打的。
        # 点击偶尔会落在弹窗别的位置，必须确认光标真的在输入框里，否则密码会打到别处
        focused = False
        for _ in range(3):
            if not click_in_dialog(PIN_TITLES, 0.47, 0.68, timeout=3):
                continue
            time.sleep(0.15)
            if self._pin_field_focused():
                focused = True
                break
            logger.warning("密码框未获得焦点，重新点击")
        if not focused:
            logger.error("密码框始终没拿到焦点，不提交（避免把密码打到别处）")
            return False

        logger.info("已聚焦密码框，使用 AiKeyboard 输入")
        if not self._input_with_aikeyboard(pin):
            logger.error("PIN 输入失败，不提交（避免用错误密码消耗次数）")
            return False

        if not fast_click_button(PIN_TITLES, ("确定", "确认", "OK"), timeout=2):
            logger.warning("未找到密码弹窗的「确定」按钮")
            return False
        logger.info("已点击密码弹窗的「确定」")

        deadline = time.time() + PIN_CLOSE_TIMEOUT
        while time.time() < deadline:
            if find_hwnd(PIN_TITLES) is None:
                logger.info("U盾密码已提交，弹窗已关闭")
                return True
            time.sleep(0.1)
        logger.warning(f"密码弹窗 {PIN_CLOSE_TIMEOUT}s 内未关闭；当前窗口: {list_windows(('密码', 'U盾'))}")
        return False

    def _wait_login_success(self, timeout: int = 60) -> bool:
        """等待登录成功；弹窗存在时不调用 Playwright。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._logged_in():
                logger.info("✓ 登录成功！")
                return True
            time.sleep(1.5)
        logger.warning(f"登录检测超时（{timeout}s）")
        return False



    def _close_home_ad(self):
        """关闭首页广告弹窗（#msgFrame 全屏遮罩），否则会挡住菜单点击。"""
        popup = None
        try:
            for frame in self.page.frames:
                if "newEiopAlertAd" in frame.url:
                    popup = frame
                    break
        except Exception:
            popup = None

        if popup is not None:
            try:
                checkbox = popup.locator(".PopPageFooter img").first
                if checkbox.count() > 0:
                    checkbox.click(timeout=1000, force=True)
                    logger.info("已勾选广告弹窗的「不再提示当前广告内容」")
            except Exception:
                pass
            try:
                self.page.evaluate(
                    """() => {
                        const frame = document.getElementById('msgFrame');
                        if (frame) { frame.style.display = 'none'; frame.src = 'about:blank'; }
                        document.querySelectorAll('.PopPage, .PopPageMask, .PopPageBg').forEach(el => el.remove());
                    }"""
                )
                logger.info("已关闭首页广告弹窗")
                return
            except Exception as exc:
                logger.debug(f"关闭首页广告弹窗失败: {exc}")

        # 兜底：通用关闭按钮
        selectors = [
            ".ad-close",
            ".ad_close",
            ".close-ad",
            "#closeAd",
            ".el-dialog__headerbtn",
            ".ant-modal-close",
        ]
        for selector in selectors:
            try:
                btn = self.page.locator(selector).first
                if btn.count() > 0 and btn.is_visible():
                    btn.click(force=True)
                    logger.info(f"已关闭首页广告: {selector}")
                    return
            except Exception:
                continue
        logger.debug("未检测到可自动关闭的首页广告")



    def close(self):
        """关闭浏览器。

        弹窗把渲染进程卡死时 context.close() 会永久挂起，采集进程就再也回传不了结果，
        平台只能干等 30 分钟租约。这里加超时兜底，超时按命令行把本次的 Chrome 杀掉。
        """
        logger.info("正在关闭浏览器...")
        for titles in (ERROR_TITLES, PIN_TITLES, CERT_TITLES):
            try:
                if find_hwnd(titles):
                    click_dialog_button(titles, ("否", "取消", "关闭"), ratio=(0.72, 0.5), timeout=1)
            except Exception:
                pass
        if self.context:
            # Playwright 同步 API 不能跨线程调用（会报 greenlet 错误），
            # 所以只在主线程关闭，另起一个定时器负责超时强杀
            watchdog = threading.Timer(8.0, self._kill_chrome_by_profile)
            watchdog.daemon = True
            watchdog.start()
            try:
                self.context.close()
            except Exception as exc:
                logger.warning(f"关闭浏览器上下文失败: {exc}")
            finally:
                watchdog.cancel()
        if self.playwright:
            try:
                self.playwright.stop()
            except Exception:
                pass
        logger.info("浏览器已关闭")
        from loguru import logger as loguru_logger
        loguru_logger.complete()

    def _kill_chrome_by_profile(self):
        target = str(getattr(self, "user_data_dir", "") or "").lower()
        if not target:
            return
        script = (
            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like '*{target}*' }} | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
        )
        try:
            _sp.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, timeout=20)
        except Exception as exc:
            logger.warning(f"强制结束 Chrome 失败: {exc}")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.config.auto_close:
            self.close()
