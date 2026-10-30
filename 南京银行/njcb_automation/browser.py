# -*- coding: utf-8 -*-
"""
浏览器管理模块 - 南京银行适配版
"""
import sys
from pathlib import Path

_keyboard_path = str(Path(__file__).resolve().parent.parent.parent / "keyboard")
sys.path.insert(0, _keyboard_path)

from playwright.sync_api import Page
from logger import setup_logger
from popup_handler import PopupHandlerMixin
from pin_inputter import PinInputterMixin
from login_manager import LoginManagerMixin
from common.base_browser import BaseBrowser

logger = setup_logger()


class NJCBBrowser(BaseBrowser, PopupHandlerMixin, PinInputterMixin, LoginManagerMixin):
    """南京银行浏览器管理器"""

    def _get_window_size(self) -> tuple:
        return (1700, 900)

    def _get_extra_launch_args(self) -> list:
        return [
            "--disable-infobars",
            "--kiosk-printing",
            "--disable-download-notification",
            "--disable-session-crashed-bubble",
        ]

    def _focus_web_page(self):
        """把焦点从 Chrome 地址栏拉回网页内容区。"""
        try:
            self.page.bring_to_front()
            self.page.mouse.click(900, 500)
            self.page.keyboard.press("Escape")
            logger.info("已点击网页空白区域，取消地址栏激活")
        except Exception as e:
            logger.debug(f"取消地址栏激活失败: {e}")

    def navigate_to_login(self):
        """导航到登录页 - 南京银行适配"""
        logger.info(f"正在访问: {self.config.login_url}")
        try:
            self.page.goto(self.config.login_url, wait_until="networkidle", timeout=30000)
        except Exception as e:
            logger.warning(f"页面加载超时，继续执行: {e}")

        import time
        time.sleep(1)

        self._close_cookie_consent()
        self._close_announcement_dialog()
        self._focus_web_page()

        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass
