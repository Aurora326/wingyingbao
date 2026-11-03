# -*- coding: utf-8 -*-
"""
浏览器管理模块 - 宁波银行适配版
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


class NBCBBrowser(BaseBrowser, PopupHandlerMixin, PinInputterMixin, LoginManagerMixin):
    """宁波银行浏览器管理器"""

    def navigate_to_login(self):
        """导航到登录页 - 宁波银行适配（处理证书弹窗 + PIN码弹窗）"""
        logger.info(f"正在访问: {self.config.login_url}")
        try:
            self.page.goto(self.config.login_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            logger.warning(f"页面加载超时，继续执行: {e}")

        import time
        import pyautogui
        pyautogui.FAILSAFE = False

        # 证书选择功能已停用（当前运行流程不涉及证书选择）
        if False:
            # 确保证书弹窗在前台
            try:
                self.page.bring_to_front()
            except Exception:
                pass

            # 等证书弹窗显示出来
            time.sleep(3)

            # 先按 Enter（如果弹窗默认按钮是确定，这一步就能点掉）
            logger.info("按 Enter 尝试确认证书弹窗...")
            pyautogui.press('return')
            time.sleep(2)

            # 使用原生窗口按钮检测来点击证书弹窗的确定按钮（避免盲坐标干扰 PIN 码对话框）
            try:
                from common.keyboard_helper import click_native_button
                cert_clicked = click_native_button(
                    button_texts=("确定", "确认", "OK", "是(Y)", "Yes"),
                    window_title_keywords=("证书", "Certificate", "安全", "Security", "选择", "Windows 安全"),
                    timeout=3.0
                )
                if cert_clicked:
                    logger.info("已点击证书弹窗确定按钮")
                    time.sleep(3)
                else:
                    logger.info("未检测到证书弹窗，继续执行")
                    time.sleep(2)
            except Exception as e:
                logger.warning(f"证书弹窗处理失败: {e}")
                # 兜底：固定坐标点击
                try:
                    screen_width, screen_height = pyautogui.size()
                    btn_x = int(screen_width * 0.50)
                    btn_y = int(screen_height * 0.22)
                    logger.info(f"兜底点击证书弹窗确定按钮: ({btn_x}, {btn_y})")
                    pyautogui.click(btn_x, btn_y)
                    time.sleep(3)
                except Exception as e2:
                    logger.warning(f"兜底点击也失败: {e2}")

        # 处理 PIN 弹窗（输入框已激活，直接输入）
        self._handle_pin_dialog()

        # 点击空白处关闭可能的遮罩层
        try:
            self.page.mouse.click(200, 200)
            self.page.keyboard.press("Escape")
        except Exception:
            pass
