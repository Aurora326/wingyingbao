# -*- coding: utf-8 -*-
"""
浏览器管理模块 - 建设银行适配版
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


class CCBBrowser(BaseBrowser, PopupHandlerMixin, PinInputterMixin, LoginManagerMixin):
    """建设银行浏览器管理器"""

    def _get_window_size(self) -> tuple:
        return (1500, 800)

    def navigate_to_login(self):
        """导航到登录页 - 建设银行适配"""
        # 设置弹窗监听：自动关闭空白弹窗
        self.page.on("popup", self._close_blank_popup)
        
        # 如果已经登录成功，直接跳过导航（重试时不需要再走登录页）
        if self._check_login_success():
            logger.info("当前已登录，跳过登录页导航")
            return
        
        logger.info(f"正在访问: {self.config.login_url}")
        try:
            self.page.goto(self.config.login_url, wait_until="domcontentloaded", timeout=30000)
            try:
                self.page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"页面加载超时，继续执行: {e}")

    def _close_blank_popup(self, popup):
        """自动关闭弹窗（公告页、空白页等）"""
        try:
            url = popup.url
            logger.info(f"检测到弹窗: {url}")
            # 延迟一点再检查 URL，等弹窗完全加载
            import time
            time.sleep(1)
            url = popup.url
            # 关闭空白页和银行公告类弹窗
            url_lower = (url or "").lower()
            if (
                url == "" or
                "about:blank" in url_lower or
                "blank" in url_lower or
                "article" in url_lower or
                "notice" in url_lower or
                "announce" in url_lower or
                "/chn/html1/subject/" in url_lower or
                "subject/" in url_lower
            ):
                logger.info(f"关闭弹窗: {url}")
                popup.close()
        except Exception as e:
            logger.debug(f"关闭弹窗时出错: {e}")

        import time
        time.sleep(2)

        # 检查页面是否加载了登录表单
        try:
            # 等待"立即登录"按钮出现
            self.page.locator('input.pbd_btn_login, input[value="立即登录"]').first.wait_for(state="visible", timeout=10000)
            logger.info("登录页面加载完成，检测到'立即登录'按钮")
        except Exception:
            logger.warning("未检测到'立即登录'按钮，页面可能未完全加载")

        # 注：弹窗处理统一在 main.py 登录成功后调用，避免重复执行
        # 不要点击页面空白处，避免误触广告轮播图
        # 仅按ESC关闭可能的弹层
        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass
