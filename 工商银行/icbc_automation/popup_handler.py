# -*- coding: utf-8 -*-
"""
弹窗处理模块 - 工商银行适配版
"""
import time
from logger import setup_logger

logger = setup_logger()


class PopupHandlerMixin:
    """弹窗处理 Mixin"""

    def _dismiss_browser_tips(self):
        """关闭工商银行页面提示"""
        try:
            self.page.keyboard.press("Escape")
            time.sleep(0.5)
        except Exception:
            pass

    def _handle_certificate_selection(self) -> bool:
        """
        处理证书选择弹窗（浏览器原生弹窗）
        使用 pyautogui 键盘操作：第一条证书默认已选中，直接按 Enter 确认
        """
        logger.info("=" * 50)
        logger.info("处理证书选择弹窗...")

        try:
            import pyautogui
            pyautogui.FAILSAFE = False

            screen_width, screen_height = pyautogui.size()
            logger.info(f"屏幕尺寸: {screen_width} x {screen_height}")

            logger.info("等待证书选择弹窗显示...")
            time.sleep(2)

            logger.info("按 Enter 键确认选中的证书...")
            pyautogui.keyDown('return')
            pyautogui.keyUp('return')
            time.sleep(2)

            logger.info("保险操作：再次按 Enter...")
            pyautogui.keyDown('return')
            pyautogui.keyUp('return')
            time.sleep(2)

            logger.info("证书选择完成")
            return True

        except ImportError:
            logger.error("未安装 pyautogui，无法自动选择证书")
            logger.info("请手动双击证书并点击确定...")
            time.sleep(20)
            return False
        except Exception as e:
            logger.error(f"证书选择失败: {e}")
            logger.info("请手动双击证书并点击确定...")
            time.sleep(20)
            return False
