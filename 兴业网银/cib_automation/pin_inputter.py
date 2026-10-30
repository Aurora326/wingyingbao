# -*- coding: utf-8 -*-
"""
PIN 自动输入模块 - 兴业银行适配版
"""
import time
from loguru import logger


class PinInputterMixin:
    """PIN 自动输入 Mixin"""

    def auto_enter_pin(self, pin: str, max_wait: int = 30) -> bool:
        """
        自动输入网盾 PIN 码（支持虚拟键盘）

        Args:
            pin: PIN 码（6-12 位数字或字母）
            max_wait: 最大等待弹窗时间（秒）

        Returns:
            是否成功输入
        """
        logger.info(f"等待网盾密码输入弹窗...")

        start = time.time()
        dialog_appeared = False

        while time.time() - start < max_wait:
            try:
                pin_input = self.page.locator("input[type='password']").first
                if pin_input.is_visible(timeout=500):
                    dialog_appeared = True
                    logger.info("检测到密码输入框，直接输入...")
                    pin_input.fill(pin)
                    time.sleep(0.5)
                    self._click_confirm_button()
                    return True
            except Exception:
                pass

            try:
                self.page.mouse.click(600, 400)
                time.sleep(0.3)
                self.page.keyboard.type(pin, delay=50)
                time.sleep(0.5)
                self.page.keyboard.press("Enter")
                logger.info("通过键盘事件输入 PIN 完成")
                return True
            except Exception as e:
                logger.debug(f"键盘输入尝试失败: {e}")

            time.sleep(1)

        if not dialog_appeared:
            logger.warning("未检测到密码输入弹窗，请手动输入")
        return False

    def auto_enter_pin_by_clicking(self, pin: str, max_wait: int = 30) -> bool:
        """
        通过点击虚拟键盘输入 PIN（使用 pyautogui 进行屏幕识别）

        Args:
            pin: PIN 码
            max_wait: 最大等待时间

        Returns:
            是否成功
        """
        try:
            import pyautogui
            import pyscreeze
        except ImportError:
            logger.warning("未安装 pyautogui，无法使用屏幕点击功能")
            return False

        logger.info(f"等待网盾密码弹窗出现...")
        logger.info(f"准备输入 PIN (长度: {len(pin)}, 内容: {pin})")
        time.sleep(2)

        try:
            import pygetwindow as gw
            windows = gw.getWindowsWithTitle('Chrome')
            if windows:
                win = windows[0]
                win.activate()
                time.sleep(0.5)
        except Exception:
            pass

        screenshot = pyautogui.screenshot()
        width, height = screenshot.size

        keyboard_region = (width // 4, height // 2, width // 2, height // 2)

        logger.info(f"正在识别虚拟键盘... PIN 长度: {len(pin)}")

        pyautogui.click(width // 2, height // 2 - 50)
        time.sleep(0.3)

        import ctypes
        VK_CAPITAL = 0x14
        caps_lock_on = ctypes.windll.user32.GetKeyState(VK_CAPITAL) & 0x0001 != 0
        logger.info(f"当前 Caps Lock 状态: {'开启' if caps_lock_on else '关闭'}")

        has_upper = any(c.isupper() for c in pin)
        if has_upper and not caps_lock_on:
            logger.info("检测到密码包含大写字母且 Caps Lock 关闭，开启 Caps Lock")
            pyautogui.press('capslock')
            time.sleep(0.3)
        elif not has_upper and caps_lock_on:
            logger.info("密码全为小写但 Caps Lock 开启，关闭 Caps Lock")
            pyautogui.press('capslock')
            time.sleep(0.3)

        logger.info(f"PIN: {'***'}")
        for i, char in enumerate(pin):
            pyautogui.press(char.lower() if char.isupper() else char)
            logger.debug(f"输入第 {i+1} 个字符: {char}")
            time.sleep(0.1)

        if has_upper and not caps_lock_on:
            time.sleep(0.3)
            pyautogui.press('capslock')
            logger.info("恢复 Caps Lock 状态")

        time.sleep(0.5)
        pyautogui.press('enter')

        logger.info("PIN 输入完成")
        return True

    def _click_confirm_button(self):
        """点击确认按钮"""
        confirm_selectors = [
            "button:has-text('确认')",
            "button:has-text('确定')",
            "button:has-text('登录')",
            "input[type='submit']",
            ".confirm-btn",
            "#confirmBtn",
        ]
        for selector in confirm_selectors:
            try:
                btn = self.page.locator(selector).first
                if btn.is_visible(timeout=1000):
                    btn.click()
                    logger.info(f"点击确认: {selector}")
                    return
            except Exception:
                continue
