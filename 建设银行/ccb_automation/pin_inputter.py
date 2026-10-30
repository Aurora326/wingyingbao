# -*- coding: utf-8 -*-
"""
PIN 自动输入模块 - 建设银行适配版
"""
import time
from logger import setup_logger
from common.keyboard_helper import input_with_aikeyboard, send_with_aikeyboard

logger = setup_logger()


class PinInputterMixin:
    """PIN 自动输入 Mixin"""

    def _handle_pin_dialog(self):
        """处理建设银行 PIN 码验证弹窗（Windows系统弹窗）"""
        logger.info("检查 PIN 码验证弹窗...")
        
        # 获取配置的 PIN
        pin = self.credentials.pin if self.credentials else None
        
        if not pin:
            logger.info("未配置 PIN 码，等待手动输入...")
            logger.info("请在网银盾弹窗中输入 PIN 码并点击确认")
            time.sleep(20)  # 给用户时间手动输入
            return
        
        logger.info(f"准备自动输入 PIN 码（长度: {len(pin)}），等待弹窗出现...")
        time.sleep(5)  # 等待弹窗完全显示
        
        try:
            import pyautogui
            pyautogui.FAILSAFE = False
            
            # ===== 证书确认后，PIN 弹窗自动弹出且输入框已是激活状态，直接输入即可 =====
            logger.info(f"直接输入 PIN 码（共 {len(pin)} 位）...")
            pyautogui.typewrite(pin, interval=0.1)
            logger.info("PIN 码已输入")
            time.sleep(1)
            
            # 按回车确认
            logger.info("按回车键确认...")
            pyautogui.press('return')
            time.sleep(3)
            
            logger.info("PIN 码输入流程完成")
            
            # 处理可能的二次确认弹窗
            time.sleep(3)
            pyautogui.press('return')
            time.sleep(1)
            
        except ImportError:
            logger.warning("未安装 pyautogui，尝试使用 AiKeyboard...")
            self._handle_pin_with_aikeyboard(pin)
        except Exception as e:
            logger.error(f"自动输入 PIN 失败: {e}")
            logger.info("请手动在弹窗中输入 PIN 码...")
            time.sleep(20)

    def _handle_pin_with_aikeyboard(self, pin: str):
        """使用 AiKeyboard 处理 PIN 弹窗"""
        try:
            logger.info("使用 AiKeyboard 输入 PIN 码...")
            
            # 等待弹窗完全显示
            time.sleep(2)
            
            # 使用 AiKeyboard 输入
            input_with_aikeyboard(pin)
            time.sleep(0.5)
            
            # 按 Tab 键切换到登录按钮，然后回车
            send_with_aikeyboard("tab")
            time.sleep(0.3)
            send_with_aikeyboard("return")
            time.sleep(3)
            
            logger.info("PIN 码已输入")
            time.sleep(3)
            
        except Exception as e:
            logger.error(f"AiKeyboard 输入 PIN 失败: {e}")
            logger.info("请手动在弹窗中输入 PIN 码...")
            time.sleep(20)
