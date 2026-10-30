# -*- coding: utf-8 -*-
"""
登录逻辑模块 - 中信银行适配版
"""
import time
from loguru import logger
from common.keyboard_helper import input_with_aikeyboard


class LoginManagerMixin:
    """登录逻辑 Mixin"""

    def login(self) -> bool:
        """执行登录流程"""
        logger.info("=" * 50)
        logger.info("开始登录流程...")
        logger.info(f"用户名: {repr(self.credentials.username)}")
        logger.info(f"密码: {'已设置' if self.credentials.password else '未设置'}")
        logger.info(f"PIN: {'已设置' if self.credentials.pin else '未设置'}")

        try:
            logger.info("等待页面加载...")
            time.sleep(3)

            logger.info("[步骤1] 等待UKey证书登录...")
            if self._try_ukey_login():
                return True

            logger.info("[步骤2] 尝试用户名密码登录...")
            login_result = self.auto_select_login_user()
            if login_result:
                time.sleep(3)
                success = self._check_login_success()
                if success:
                    return True

            logger.warning("自动登录失败，等待手动登录...")
            return False

        except Exception as e:
            logger.error(f"登录异常: {e}")
            return False

    def _try_ukey_login(self) -> bool:
        """中信银行 UKey 登录：直接在页面输入密码"""
        logger.info("中信银行 UKey 登录...")
        try:
            if not self.credentials.pin:
                logger.warning("未配置 PIN，跳过自动输入")
                return False

            time.sleep(2)

            try:
                if hasattr(self, "_focus_web_page"):
                    self._focus_web_page()
                else:
                    self.page.mouse.click(900, 500)
                logger.info("已点击页面空白区域，确保导航栏不处于激活状态")
                time.sleep(1)
            except Exception as e:
                logger.debug(f"点击空白区域失败: {e}")

            try:
                ukey_tab = self.page.locator("text=U盾登录").first
                if ukey_tab.count() > 0 and ukey_tab.is_visible(timeout=2000):
                    tab_classes = ukey_tab.evaluate("el => el.className")
                    if 'active' not in tab_classes:
                        logger.info("切换到 U盾登录 标签...")
                        ukey_tab.click()
                        time.sleep(1)
                    else:
                        logger.info("已在 U盾登录 标签")
            except Exception as e:
                logger.debug(f"切换标签页检查: {e}")

            pin_selectors = [
                "#passwordId",
                "input#passwordId",
                "[id='passwordId']",
                "input[placeholder*='登录密码']",
                "input[type='password']",
            ]

            pwd_input = None
            found_selector = None

            for attempt in range(3):
                logger.info(f"第 {attempt + 1} 次尝试查找密码输入框...")

                for selector in pin_selectors:
                    try:
                        element = self.page.locator(selector).first
                        if element.count() > 0 and element.is_visible(timeout=2000):
                            pwd_input = element
                            found_selector = selector
                            logger.info(f"找到登录密码输入框: {selector}")
                            break
                    except Exception:
                        continue

                if pwd_input:
                    break
                time.sleep(2)

            if not pwd_input:
                logger.warning("未找到登录密码输入框，尝试 JavaScript...")
                result = self.page.evaluate("""() => {
                    const pwd = document.getElementById('passwordId') || 
                               document.querySelector('input[type="password"]');
                    if (pwd) {
                        pwd.focus();
                        pwd.click();
                        return {found: true};
                    }
                    return {found: false};
                }""")
                if result and result.get('found'):
                    if input_with_aikeyboard(self.credentials.pin):
                        logger.info("AiKeyboard 输入完成")
                    else:
                        self.page.evaluate("""
                            (pin) => {
                                const pwd = document.getElementById('passwordId');
                                if (pwd) pwd.value = pin;
                            }
                        """, self.credentials.pin)
                else:
                    return False
            else:
                logger.info(f"点击密码输入框 ({found_selector})...")
                pwd_input.click()
                time.sleep(2)

                if input_with_aikeyboard(self.credentials.pin):
                    logger.info("使用 AiKeyboard 输入密码完成")
                else:
                    logger.warning("AiKeyboard 失败，使用 Playwright 方式...")
                    pwd_input.fill(self.credentials.pin)
            time.sleep(0.5)

            self._click_login_button()
            logger.info("已点击登录按钮，等待登录结果...")

            if self.cabinet_controller:
                logger.info("等待 USBKey 屏幕显示交易信息...")
                time.sleep(3)
                logger.info("使用机柜点击物理 USBKey OK键...")
                self.cabinet_controller.click_ok()
                time.sleep(3)

            time.sleep(5)
            if self._check_login_success():
                logger.info("✓ 登录成功！")
                return True

            start = time.time()
            while time.time() - start < 15:
                if self._check_login_success():
                    logger.info("✓ 登录成功！")
                    return True
                time.sleep(1)

            logger.warning("登录可能还在处理中或需要手动确认")
            return False

        except Exception as e:
            logger.error(f"UKey 登录失败: {e}")
            return False

    def auto_select_login_user(self, user_name: str = None, password: str = None) -> bool:
        """自动选择登录用户并输入密码"""
        user_name = user_name or self.credentials.username
        password = password or self.credentials.password

        logger.info("尝试自动登录...")
        logger.info(f"user_name: {repr(user_name)}, password: {'已设置' if password else '未设置'}")

        if not user_name:
            logger.warning("未配置登录用户名，跳过自动选择")
            return False

        try:
            time.sleep(2)

            login_selectors = [
                "input[name='username']",
                "input[id='username']",
                "input[placeholder*='用户名']",
                "input[placeholder*='操作员']",
                ".login-input",
            ]

            login_input = None
            for selector in login_selectors:
                try:
                    element = self.page.locator(selector).first
                    if element.is_visible(timeout=3000):
                        login_input = element
                        logger.info(f"找到登录名输入框: {selector}")
                        break
                except Exception:
                    continue

            if not login_input:
                logger.warning("未找到登录名输入框，可能页面使用UKey证书登录")
                return False

            login_input.fill(user_name)
            logger.info(f"已输入用户名: {user_name}")
            time.sleep(0.5)

            if password:
                pwd_selectors = [
                    "input[name='password']",
                    "input[id='password']",
                    "input[type='password']",
                ]

                pwd_input = None
                for selector in pwd_selectors:
                    try:
                        element = self.page.locator(selector).first
                        if element.is_visible(timeout=2000):
                            pwd_input = element
                            logger.info(f"找到密码输入框: {selector}")
                            break
                    except Exception:
                        continue

                if pwd_input:
                    pwd_input.fill(password)
                    logger.info("已输入登录密码")
                    time.sleep(0.5)

            self._click_login_button()
            logger.info("已点击登录按钮")

            time.sleep(3)
            return True

        except Exception as e:
            logger.error(f"自动登录失败: {e}")
            return False

    def _click_login_button(self):
        """点击登录按钮"""
        selectors = [
            "#loginFormContent > button",
            "#loginFormContent button",
            "button:has-text('登录')",
            "button:has-text('确定')",
            "input[type='submit']",
            "a:has-text('登录')",
            ".login-btn",
            "#loginBtn",
            "button[type='submit']",
        ]
        for selector in selectors:
            try:
                btn = self.page.locator(selector).first
                if btn.is_visible(timeout=2000):
                    logger.info(f"点击登录按钮: {selector}")
                    btn.click()
                    return
            except Exception:
                continue

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

    def _check_login_success(self) -> bool:
        """检查登录是否成功"""
        success_selectors = [
            ".user-name",
            ".welcome",
            "#main-frame",
            ".main-content",
            "text=欢迎",
            "text=首页",
            ".logout",
            "a:has-text('退出')",
            ".user-info",
            ".account-info",
        ]
        for selector in success_selectors:
            try:
                if self.page.locator(selector).first.is_visible(timeout=3000):
                    logger.info(f"检测到登录成功元素: {selector}")
                    return True
            except Exception:
                continue

        current_url = self.page.url
        if "login" not in current_url.lower() and "cotb" in current_url.lower():
            logger.info(f"检测到 URL 已变化，可能已登录: {current_url}")
            return True

        return False

    def wait_for_manual_login(self, timeout: int = 180) -> bool:
        """等待用户手动完成登录（自动检测登录状态）"""
        print("\n" + "=" * 60)
        print("手动登录指引")
        print("1. 插入中信银行UKey数字证书")
        print("2. 在浏览器中选择证书并输入UKey密码")
        print("3. 程序将自动检测登录状态，无需手动按 Enter")
        print("=" * 60)

        logger.info("正在等待登录完成，自动检测中...")
        start = time.time()
        check_interval = 2

        while time.time() - start < timeout:
            if self._check_login_success():
                logger.info("✓ 检测到登录成功！")
                return True

            elapsed = int(time.time() - start)
            if elapsed % 5 == 0:
                remaining = timeout - elapsed
                logger.info(f"等待登录中... ({elapsed}s / {timeout}s)，剩余 {remaining}s")

            time.sleep(check_interval)

        logger.warning("登录检测超时")
        print(f"\n⚠ 在 {timeout} 秒内未检测到登录成功")
        try:
            response = input("是否继续等待？(y/n): ").strip().lower()
            if response in ('y', 'yes'):
                return self.wait_for_manual_login(timeout=60)
        except KeyboardInterrupt:
            pass
        return False
