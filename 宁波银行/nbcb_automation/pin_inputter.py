# -*- coding: utf-8 -*-
"""
PIN 自动输入模块 - 宁波银行适配版
"""
import time
from logger import setup_logger
from common.keyboard_helper import input_with_aikeyboard, send_with_aikeyboard

logger = setup_logger()


class PinInputterMixin:
    """PIN 自动输入 Mixin"""

    def _handle_pin_dialog(self):
        """处理宁波银行 PIN 码验证弹窗（Windows系统弹窗）"""
        logger.info("检查 PIN 码验证弹窗...")
        
        # 获取配置的 PIN
        pin = self.credentials.pin if self.credentials else None
        
        if not pin:
            logger.info("未配置 PIN 码，等待手动输入...")
            logger.info("请在弹窗中输入 PIN 码并点击登录")
            time.sleep(20)  # 给用户时间手动输入
            return
        
        logger.info(f"准备自动输入 PIN 码（长度: {len(pin)}），等待弹窗出现...")
        time.sleep(3)  # 等待弹窗完全显示
        
        try:
            import pyautogui
            pyautogui.FAILSAFE = False
            
            # 获取屏幕尺寸
            screen_width, screen_height = pyautogui.size()
            center_x = screen_width // 2
            center_y = screen_height // 2
            
            # 1. 按 3 下 Tab 键激活 PIN 输入框
            logger.info("按 3 下 Tab 键激活 PIN 输入框...")
            for i in range(3):
                pyautogui.press('tab')
                time.sleep(1)
            time.sleep(1)  # 等待 1 秒让输入框获得焦点
            
            # 2. 清除可能已有的内容（多次确保清空）
            logger.info("清除输入框内容...")
            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.3)
            pyautogui.press('delete')
            time.sleep(0.3)
            
            # 3. 使用 pyautogui 逐个字符慢速输入 PIN 码
            logger.info(f"开始慢速输入 PIN 码（共 {len(pin)} 位）...")
            for i, char in enumerate(pin):
                pyautogui.typewrite(char, interval=0.05)
                logger.info(f"  输入第 {i+1} 位: {char}")
                time.sleep(0.5)  # 每个字符后等待 0.5 秒
            
            logger.info("PIN 码输入完成，等待 2 秒...")
            time.sleep(2)
            
            # 4. 按回车键确认
            logger.info("按回车键确认...")
            pyautogui.press('return')
            time.sleep(3)
            
            logger.info("PIN 码输入流程完成")
            
            # 如果配置了机柜控制器，点击物理 UKey OK键
            if self.cabinet_controller:
                logger.info("等待 USBKey 屏幕显示交易信息...")
                time.sleep(3)
                logger.info("使用机柜点击物理 USBKey OK键...")
                self.cabinet_controller.click_ok()
                time.sleep(3)
            
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
            
            logger.info("PIN 码已输入，等待 USBKey 提示框...")
            time.sleep(3)
            
            # 如果配置了机柜控制器，点击物理 UKey OK键
            if self.cabinet_controller:
                logger.info("等待 USBKey 屏幕显示交易信息...")
                time.sleep(3)
                logger.info("使用机柜点击物理 USBKey OK键...")
                self.cabinet_controller.click_ok()
                time.sleep(3)
            
        except Exception as e:
            logger.error(f"AiKeyboard 输入 PIN 失败: {e}")
            logger.info("请手动在弹窗中输入 PIN 码...")
            time.sleep(20)
