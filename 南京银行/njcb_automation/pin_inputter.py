# -*- coding: utf-8 -*-
"""
PIN 自动输入模块 - 南京银行适配版
"""
import time
from loguru import logger
from common.keyboard_helper import input_with_aikeyboard


class PinInputterMixin:
    """PIN 自动输入 Mixin"""

    def auto_enter_pin(self, pin: str, max_wait: int = 30) -> bool:
        """
        南京银行：在登录页面输入密码（Playwright方式）
        """
        logger.info("南京银行：在登录页面输入密码...")
        time.sleep(2)

        try:
            password_selectors = [
                "input[type='password']",
                "input[placeholder*='密码']",
                "input[name='password']",
                "input[name='pin']",
                "input[id='password']",
                "input[id='pin']",
                ".login-form input[type='password']",
                "input[placeholder*='PIN']",
            ]

            for selector in password_selectors:
                try:
                    pwd_input = self.page.locator(selector).first
                    if pwd_input.count() > 0 and pwd_input.is_visible(timeout=3000):
                        pwd_input.fill(pin)
                        logger.info(f"已输入密码: {selector}")
                        time.sleep(0.5)
                        self._click_login_button()
                        logger.info("已点击登录按钮")
                        return True
                except Exception:
                    continue

            logger.warning("未找到密码输入框")
            return False

        except Exception as e:
            logger.error(f"输入密码失败: {e}")
            return False

    def auto_enter_pin_by_clicking(self, pin: str, max_wait: int = 30) -> bool:
        """
        南京银行：使用 AiKeyboard 输入密码
        """
        logger.info("南京银行模式：使用 AiKeyboard 输入密码...")
        time.sleep(3)

        try:
            try:
                if hasattr(self, "_focus_web_page"):
                    self._focus_web_page()
                else:
                    self.page.mouse.click(900, 500)
                logger.info("已点击页面空白区域，确保导航栏不处于激活状态")
                time.sleep(1)
            except Exception as e:
                logger.debug(f"点击空白区域失败: {e}")

            password_selectors = [
                "input[type='password']",
                "input[placeholder*='密码']",
                "input[name='password']",
                "input[name='pin']",
                "input[id='password']",
                "input[id='pin']",
                "input[placeholder*='PIN']",
            ]

            pwd_input = None
            found_selector = None

            for attempt in range(3):
                logger.info(f"第 {attempt + 1} 次尝试查找密码输入框...")

                for selector in password_selectors:
                    try:
                        element = self.page.locator(selector).first
                        count = element.count()
                        logger.debug(f"选择器 {selector}: count={count}")

                        if count > 0:
                            try:
                                element.scroll_into_view_if_needed()
                            except Exception:
                                pass

                            if element.is_visible(timeout=2000):
                                pwd_input = element
                                found_selector = selector
                                logger.info(f"找到密码输入框: {selector}")
                                break
                    except Exception as e:
                        logger.debug(f"选择器 {selector} 异常: {e}")
                        continue

                if pwd_input:
                    break
                time.sleep(2)

            if not pwd_input:
                logger.warning("未找到密码输入框，尝试使用 JavaScript 查找...")
                result = self.page.evaluate("""() => {
                    const pwd = document.querySelector('input[type="password"]');
                    if (pwd) {
                        pwd.focus();
                        pwd.click();
                        return {found: true, id: pwd.id, name: pwd.name, tag: pwd.tagName};
                    }
                    return {found: false};
                }""")
                logger.info(f"JavaScript 查找结果: {result}")

                if result and result.get('found'):
                    time.sleep(0.5)
                    if input_with_aikeyboard(pin):
                        time.sleep(0.5)
                        self._click_login_button()
                        return True
                return False

            try:
                if hasattr(self, "_focus_web_page"):
                    self._focus_web_page()
                else:
                    self.page.mouse.click(900, 500)
                logger.info("已点击页面空白区域")
                time.sleep(0.5)
                self.page.keyboard.press("Escape")
                logger.info("已按 Esc 键确保退出地址栏编辑模式")
                time.sleep(0.3)
                self.page.mouse.click(400, 400)
                logger.info("已点击页面中心区域")
                time.sleep(0.5)
            except Exception as e:
                logger.debug(f"焦点处理失败: {e}")

            logger.info(f"点击密码输入框 ({found_selector})...")
            pwd_input.click()
            time.sleep(2)

            logger.info("调用 AiKeyboard 输入密码...")
            if input_with_aikeyboard(pin):
                logger.info("AiKeyboard 输入完成，等待一下再点击登录...")
                time.sleep(1)
                self._click_login_button()
                return True

            logger.warning("AiKeyboard 失败，使用 Playwright 方式...")
            pwd_input.fill(pin)
            time.sleep(0.5)
            self._click_login_button()
            return True

        except Exception as e:
            logger.error(f"输入密码失败: {e}")
            return False
