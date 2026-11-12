# -*- coding: utf-8 -*-
"""
浏览器基础类 - 提取各银行 browser.py 的公共生命周期逻辑
"""
import os
import sys
import subprocess
from pathlib import Path
from typing import Optional
from playwright.sync_api import sync_playwright, Page
from loguru import logger as _loguru_logger

logger = _loguru_logger


class BaseBrowser:
    """浏览器自动化基础类"""

    def __init__(self, browser_config, credentials=None, mb=None, cabinet_config=None, captcha_config=None):
        self.config = browser_config
        self.credentials = credentials
        self.mb = mb
        self.captcha_config = captcha_config
        self.playwright = None
        self.browser = None
        self.context = None
        self.page: Optional[Page] = None
        self.cabinet_controller = None
        self.user_data_dir: Optional[Path] = None

        cabinet_cfg = cabinet_config or mb
        if cabinet_cfg:
            try:
                from cabinet_controller import create_cabinet_controller, CabinetControllerFactory
                self.cabinet_controller = create_cabinet_controller(cabinet_cfg)
                if self.cabinet_controller:
                    config_dict = CabinetControllerFactory._config_to_dict(cabinet_cfg)
                    logger.info(f"机柜控制器已初始化: {config_dict.get('type', 'unknown')}")
            except Exception as e:
                logger.warning(f"机柜控制器初始化失败: {e}")

    def _find_chrome_path(self) -> Optional[str]:
        """查找 Chrome/Edge/Chromium 路径"""
        if getattr(sys, 'frozen', False):
            base_dir = Path(sys.executable).parent
            bundled_chromium = base_dir / "chromium" / "chrome.exe"
            if bundled_chromium.exists():
                logger.info(f"使用打包的 Chromium: {bundled_chromium}")
                return str(bundled_chromium)

        paths = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        ]
        if self.config.chrome_path and Path(self.config.chrome_path).exists():
            return self.config.chrome_path
        for p in paths:
            if Path(p).exists():
                return p
        return None

    def _kill_all_browser_processes(self):
        """强制关闭本机所有 Chrome/Edge 浏览器进程"""
        logger.info("正在强制关闭所有 Chrome/Edge 进程...")
        try:
            subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], capture_output=True)
            subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"], capture_output=True)
            logger.info("所有 Chrome/Edge 进程已关闭")
        except Exception as e:
            logger.warning(f"关闭浏览器进程失败: {e}")

    def _kill_our_browser_processes(self):
        """只关闭使用本程序 user-data-dir 的浏览器进程，不影响用户自己打开的浏览器。"""
        if not self.user_data_dir:
            return
        marker = str(self.user_data_dir).lower().replace("'", "''")
        script = (
            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe' or Name='msedge.exe'\" | "
            f"Where-Object {{ $_.CommandLine -and $_.CommandLine.ToLower().Contains('{marker}') }} | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                timeout=15,
            )
        except Exception as e:
            logger.warning(f"清理采集浏览器进程失败: {e}")

    def _get_base_dir(self) -> Path:
        if getattr(sys, 'frozen', False):
            return Path(sys.executable).parent
        return Path(__file__).parent

    def _get_window_size(self) -> tuple:
        return (1750, 900)

    def _get_extra_launch_args(self) -> list:
        return [
            "--hide-crash-restore-bubble",
            "--noerrdialogs",
        ]

    def _get_init_script(self) -> Optional[str]:
        return """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """

    def _fix_chrome_preferences(self, user_data_dir: Path):
        """修正 Chrome 配置：清理恢复状态，并重置窗口尺寸，避免持久化上下文恢复旧窗口大小。"""
        prefs_path = user_data_dir / "Default" / "Preferences"
        if not prefs_path.exists():
            return
        try:
            import json
            with open(prefs_path, 'r', encoding='utf-8') as f:
                prefs = json.load(f)
            profile = prefs.get("profile", {})
            if "exit_type" in profile:
                profile["exit_type"] = "Normal"
            if "exited_cleanly" in profile:
                profile["exited_cleanly"] = True
            if "exit_type" in prefs:
                prefs["exit_type"] = "Normal"
            if "exited_cleanly" in prefs:
                prefs["exited_cleanly"] = True

            # 重置窗口位置和大小，确保使用 _get_window_size() 的设定
            width, height = self._get_window_size()
            browser_prefs = prefs.setdefault("browser", {})
            window_placement = browser_prefs.setdefault("window_placement", {})
            window_placement["left"] = 0
            window_placement["top"] = 0
            window_placement["right"] = width
            window_placement["bottom"] = height
            window_placement["maximized"] = False
            window_placement["always_on_top"] = False

            profile = prefs.setdefault("profile", {})

            with open(prefs_path, 'w', encoding='utf-8') as f:
                json.dump(prefs, f)
            logger.info(f"已修正 Chrome 恢复状态并重置窗口尺寸为 {width}x{height}")
        except Exception as e:
            logger.debug(f"修正 Chrome 偏好设置失败: {e}")

    def _pre_launch_setup(self, chrome_path: Optional[str], base_dir: Path, launch_options: dict):
        """子类可覆写以添加扩展、ignore_default_args 等"""
        pass

    def _post_start(self):
        """子类可覆写以执行扩展检查等"""
        pass

    def start(self) -> Page:
        """启动浏览器并返回页面对象"""
        logger.info("正在启动浏览器...")
        self.playwright = sync_playwright().start()

        chrome_path = self._find_chrome_path()
        if chrome_path:
            logger.info(f"使用 Chrome: {chrome_path}")

        base_dir = self._get_base_dir()
        user_data_dir = base_dir / "chrome_user_data"
        user_data_dir.mkdir(exist_ok=True)
        self.user_data_dir = user_data_dir
        # 清理恢复页面标记，避免强制关闭后下次弹出恢复对话框
        self._fix_chrome_preferences(user_data_dir)
        logger.info(f"用户数据目录: {user_data_dir}")

        window_width, window_height = self._get_window_size()

        args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            f"--window-size={window_width},{window_height}",
            "--ignore-certificate-errors",
            "--disable-popup-blocking",
            "--safebrowsing-disable-download-protection",
        ] + self._get_extra_launch_args()

        launch_options = {
            "headless": self.config.headless,
            # 不用视口模拟：视口会把窗口撑成 视口高度 + 浏览器工具栏高度，
            # 关掉后 --window-size 才是整个窗口的实际尺寸
            "no_viewport": True,
            "args": args,
        }
        if chrome_path:
            launch_options["executable_path"] = chrome_path

        if getattr(sys, 'frozen', False):
            download_path = Path(sys.executable).parent / "downloads"
        else:
            download_path = Path(__file__).parent / "downloads"
        download_path.mkdir(exist_ok=True)
        launch_options["downloads_path"] = str(download_path.resolve())
        logger.info(f"浏览器下载路径: {download_path}")
        self.download_path = download_path

        self._pre_launch_setup(chrome_path, base_dir, launch_options)

        self.context = self.playwright.chromium.launch_persistent_context(
            str(user_data_dir),
            **launch_options
        )

        pages = self.context.pages
        if pages:
            self.page = pages[0]
        else:
            self.page = self.context.new_page()

        # 不再自动最大化窗口，保持 _get_window_size() 设定的尺寸
        try:
            self.page.evaluate("window.moveTo(0, 0);")
        except Exception:
            pass

        # 持久化上下文可能恢复上次的窗口尺寸，这里按配置再校正一次
        try:
            self.page.evaluate(
                "(size) => { window.moveTo(0, 0); window.resizeTo(size[0], size[1]); }",
                [window_width, window_height],
            )
            logger.info(f"浏览器窗口尺寸: {window_width}x{window_height}")
        except Exception as e:
            logger.warning(f"设置浏览器窗口尺寸失败: {e}")

        self.page.set_default_timeout(self.config.page_timeout * 1000)

        init_script = self._get_init_script()
        if init_script:
            self.page.add_init_script(init_script)

        logger.info("浏览器已启动")

        self._post_start()
        return self.page

    def close(self, kill_all_chrome: bool = False):
        """关闭浏览器。默认只关闭本程序启动的实例，不影响用户自己的浏览器。"""
        logger.info("正在关闭浏览器...")
        try:
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
        finally:
            if kill_all_chrome:
                self._kill_all_browser_processes()
            else:
                self._kill_our_browser_processes()
        logger.info("浏览器已关闭")
        _loguru_logger.complete()
