# -*- coding: utf-8 -*-
"""
PIN 自动输入模块 - 宁波银行财资云适配版
"""
import time
from logger import setup_logger
from common.keyboard_helper import input_with_aikeyboard, send_with_aikeyboard

logger = setup_logger()


def _switch_to_english_input():
    """检查并切换输入法为英文状态"""
    try:
        import ctypes
        import pyautogui

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        thread_id = kernel32.GetCurrentThreadId()
        current_layout = user32.GetKeyboardLayout(thread_id)
        current_lang_id = current_layout & 0xFFFF

        ENGLISH_LAYOUTS = {0x0409, 0x0809}  # 美式英语、英式英语

        if current_lang_id in ENGLISH_LAYOUTS:
            logger.info(f"【PIN弹窗】当前已是英文输入法 (0x{current_lang_id:04X})")
            return True

        logger.info(f"【PIN弹窗】当前输入法非英文 (0x{current_lang_id:04X})，尝试切换...")
        # 按 Shift 切换输入法状态（常见输入法的中英文切换）
        pyautogui.press('shift')
        time.sleep(0.3)

        # 再次检查
        current_layout = user32.GetKeyboardLayout(thread_id)
        current_lang_id = current_layout & 0xFFFF
        if current_lang_id in ENGLISH_LAYOUTS:
            logger.info(f"【PIN弹窗】切换后已是英文输入法 (0x{current_lang_id:04X})")
            return True

        # 如果还是不行，尝试 Alt+Shift 切换键盘布局
        logger.info("【PIN弹窗】Shift 切换无效，尝试 Alt+Shift...")
        pyautogui.keyDown('alt')
        pyautogui.keyDown('shift')
        pyautogui.keyUp('shift')
        pyautogui.keyUp('alt')
        time.sleep(0.3)

        current_layout = user32.GetKeyboardLayout(thread_id)
        current_lang_id = current_layout & 0xFFFF
        logger.info(f"【PIN弹窗】Alt+Shift 切换后输入法 (0x{current_lang_id:04X})")
        return True
    except Exception as e:
        logger.warning(f"【PIN弹窗】输入法切换失败: {e}")
        return False


class PinInputterMixin:
    """PIN 自动输入 Mixin"""

    def _handle_pin_dialog(self):
        """处理 PIN 码验证弹窗：AiKeyboard 逐字符输入，回车确认"""
        logger.info("=" * 60)
        logger.info("【PIN弹窗】开始处理 PIN 码验证弹窗...")

        pin = self.credentials.pin if self.credentials else None
        if not pin:
            logger.info("【PIN弹窗】未配置 PIN 码，等待手动输入...")
            time.sleep(20)
            return

        logger.info(f"【PIN弹窗】配置 PIN 码长度: {len(pin)}，等待弹窗出现...")
        time.sleep(5)

        try:
            # 输入前确保英文输入法
            _switch_to_english_input()
            time.sleep(0.5)

            logger.info(f"【PIN弹窗】使用 AiKeyboard 按配置原文输入 PIN（共 {len(pin)} 位）...")
            success = input_with_aikeyboard(pin)
            if not success:
                logger.warning("【PIN弹窗】整串输入失败，改用逐字符输入...")
                for i, char in enumerate(pin):
                    key = char
                    char_type = "大写字母" if char.isupper() else ("小写字母" if char.islower() else ("数字" if char.isdigit() else "其他"))
                    logger.info(f"【PIN弹窗】  准备发送第 {i + 1}/{len(pin)} 位 (类型: {char_type})")
                    success = send_with_aikeyboard(key)
                    if not success:
                        logger.warning(f"【PIN弹窗】  第 {i + 1}/{len(pin)} 位发送失败！")
                    time.sleep(0.8)
                    logger.info(f"【PIN弹窗】  第 {i + 1}/{len(pin)} 位已发送")

            logger.info("【PIN弹窗】PIN 码输入完成，按回车确认...")
            time.sleep(0.5)
            send_with_aikeyboard("Enter")
            time.sleep(5)
            logger.info("【PIN弹窗】处理完成")

        except Exception as e:
            logger.error(f"【PIN弹窗】AiKeyboard 输入失败: {e}")
            logger.info("【PIN弹窗】请手动输入 PIN 码...")
            time.sleep(20)

        logger.info("=" * 60)
