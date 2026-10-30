# -*- coding: utf-8 -*-
"""
浏览器管理模块 - 广发银行适配版
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


class CGBBrowser(BaseBrowser, PopupHandlerMixin, PinInputterMixin, LoginManagerMixin):
    """广发银行浏览器管理器"""

    def _get_window_size(self) -> tuple:
        return (1500, 800)

    def navigate_to_login(self):
        """导航到登录页 - 广发银行适配（处理PIN码弹窗）"""
        logger.info(f"正在访问: {self.config.login_url}")
        try:
            self.page.goto(self.config.login_url, wait_until="domcontentloaded", timeout=30000)
            try:
                self.page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"页面加载超时，继续执行: {e}")

        import time
        time.sleep(0.5)

        self._handle_pin_dialog()

        try:
            self.page.mouse.click(200, 200)
            self.page.keyboard.press("Escape")
        except Exception:
            pass
