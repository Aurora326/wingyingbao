# -*- coding: utf-8 -*-
"""
PIN 自动输入模块 - 工商银行适配版
"""
import time
from logger import setup_logger
from common.keyboard_helper import input_with_aikeyboard, send_with_aikeyboard

logger = setup_logger()


class PinInputterMixin:
    """PIN 自动输入 Mixin"""

    def _ensure_english_input_method(self):
        """强制切换到英文输入法（美国键盘），避免中文输入法影响密码输入"""
        try:
            import ctypes
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            hkl = user32.LoadKeyboardLayoutW("00000409", 1)
            if hkl:
                user32.ActivateKeyboardLayout(hkl, 0)
                logger.info("已切换到英文输入法")
            else:
                logger.warning("切换英文输入法失败")
        except Exception as e:
            logger.warning(f"切换英文输入法时出错: {e}")

    def _handle_ukey_password_popup(self, pin: str) -> bool:
        """
        处理校验U盾密码弹窗
        使用 AiKeyboard 输入密码（U盾安全控件通常需要虚拟键盘），pyautogui 按 Enter 确认
        """
        logger.info("=" * 50)
        logger.info("处理U盾密码弹窗...")

        if not pin:
            logger.info("未配置 PIN 码，等待手动输入...")
            time.sleep(30)
            return False

        try:
            import pyautogui
            pyautogui.FAILSAFE = False

            logger.info("等待U盾密码弹窗显示...")
            time.sleep(2)

            self._ensure_english_input_method()
            time.sleep(0.5)

            screen_w, screen_h = pyautogui.size()
            input_x = int(screen_w * 0.5)
            input_y = int(screen_h * 0.44)
            logger.info(f"点击密码输入框位置: ({input_x}, {input_y})")
            pyautogui.click(input_x, input_y)
            time.sleep(0.5)

            self._ensure_english_input_method()
            time.sleep(0.3)

            logger.info(f"使用 AiKeyboard 输入U盾密码（共 {len(pin)} 位）...")
            if not input_with_aikeyboard(pin):
                logger.error("AiKeyboard 输入失败")
                return False

            time.sleep(0.5)

            logger.info("按 Enter 键确认...")
            pyautogui.keyDown('return')
            pyautogui.keyUp('return')
            time.sleep(2)

            pyautogui.keyDown('return')
            pyautogui.keyUp('return')
            time.sleep(2)

            logger.info("U盾密码输入完成")
            return True

        except ImportError:
            logger.warning("未安装 pyautogui，尝试使用纯 AiKeyboard...")
            return self._handle_ukey_password_with_aikeyboard(pin)
        except Exception as e:
            logger.error(f"U盾密码输入失败: {e}")
            logger.info("请手动在弹窗中输入 U盾 密码...")
            time.sleep(30)
            return False

    def _handle_ukey_password_with_aikeyboard(self, pin: str) -> bool:
        """使用 AiKeyboard 处理 U盾密码弹窗"""
        try:
            logger.info("使用 AiKeyboard 输入 U盾 密码...")
            time.sleep(2)
            self._ensure_english_input_method()
            time.sleep(0.5)
            input_with_aikeyboard(pin)
            time.sleep(0.5)
            send_with_aikeyboard("return")
            time.sleep(3)

            logger.info("PIN 码已输入")
            return True

        except Exception as e:
            logger.error(f"AiKeyboard 输入 PIN 失败: {e}")
            logger.info("请手动在弹窗中输入 U盾 密码...")
            time.sleep(30)
            return False
