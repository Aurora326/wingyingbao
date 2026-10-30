# -*- coding: utf-8 -*-
"""
登录逻辑模块 - 兴业银行适配版
"""
import time
from loguru import logger


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

            logger.info("[步骤1] 尝试 UKey 证书登录...")
            if self._try_ukey_login():
                return True

            logger.info("[步骤2] 尝试自动选择登录用户...")
            login_result = self.auto_select_login_user()
            logger.info(f"auto_select_login_user 结果: {login_result}")
            if login_result:
                time.sleep(3)
                success = self._check_login_success()
                logger.info(f"_check_login_success 结果: {success}")
                if success:
                    return True

            logger.info("[步骤3] 尝试用户名密码登录...")
            if self._try_password_login():
                return True

            logger.warning("自动登录均失败，等待手动登录...")
            manual_timeout = 120
            start = time.time()
            while time.time() - start < manual_timeout:
                if self._check_login_success():
                    logger.info("手动登录成功！")
                    return True
                time.sleep(2)
            return False
        except Exception as e:
            logger.error(f"登录异常: {e}")
            return False

    def wait_for_manual_login(self, timeout: int = 180) -> bool:
        """等待用户手动完成登录（自动检测登录状态）"""
        print("\n" + "=" * 60)
        print("手动登录指引")
        print("1. 插入 CFCA 数字证书 UKey")
        print("2. 在浏览器中完成证书选择和 UKey 密码输入")
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

    def auto_select_login_user(self, user_name: str = None, password: str = None) -> bool:
        """自动选择登录用户并输入密码"""
        user_name = user_name or self.credentials.username
        password = password or self.credentials.password

        logger.info(f"auto_select_login_user 被调用")
        logger.info(f"user_name: {repr(user_name)}, password: {'已设置' if password else '未设置'}")

        if not user_name:
            logger.warning("未配置登录用户名，跳过自动选择")
            return False

        logger.info(f"尝试自动选择登录用户: {user_name}")

        try:
            time.sleep(2)

            login_selectors = [
                "#loginName",
                "input#loginName",
                "[placeholder*='登录名']",
                ".el-input__inner",
                "input[type='text']",
            ]

            login_input = None
            logger.info(f"查找登录名输入框...")

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
                logger.warning("未找到登录名输入框")
                return False

            logger.info("点击登录名输入框展开下拉选项...")
            login_input.click()
            time.sleep(2)

            try:
                self.page.screenshot(path="debug_login_dropdown.png")
                logger.info("已保存下拉菜单截图: debug_login_dropdown.png")
            except Exception:
                pass

            logger.info(f"尝试选择用户: {user_name}")
            selected = False

            try:
                user_option = self.page.locator(f"text={user_name}").first
                if user_option.is_visible(timeout=2000):
                    user_option.click()
                    logger.info(f"✓ 方法1成功 - 已点击选择用户: {user_name}")
                    selected = True
            except Exception as e1:
                logger.debug(f"方法1失败: {e1}")

            if not selected:
                dropdown_selectors = [
                    f".el-select-dropdown__item:has-text('{user_name}')",
                    f".el-dropdown-menu__item:has-text('{user_name}')",
                    f"li:has-text('{user_name}')",
                    f".select-item:has-text('{user_name}')",
                    f"[class*='dropdown']:has-text('{user_name}')",
                    f"[class*='select']:has-text('{user_name}')",
                ]
                for selector in dropdown_selectors:
                    try:
                        option = self.page.locator(selector).first
                        if option.is_visible(timeout=1000):
                            option.click()
                            logger.info(f"✓ 方法2成功 - 使用选择器 {selector} 选择用户: {user_name}")
                            selected = True
                            break
                    except Exception:
                        continue

            if not selected:
                try:
                    logger.info("方法3 - 直接输入用户名...")
                    login_input.fill(user_name)
                    time.sleep(0.5)
                    self.page.keyboard.press("Enter")
                    logger.info(f"✓ 方法3成功 - 已输入用户名: {user_name}")
                    selected = True
                except Exception as e3:
                    logger.warning(f"方法3失败: {e3}")

            if not selected:
                logger.warning(f"未能自动选择用户: {user_name}，请手动选择")

            time.sleep(0.5)

            if password:
                pwd_selectors = [
                    "#password",
                    "input#password",
                    "input[placeholder*='登录密码']",
                    "input[placeholder*='密码']",
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
                    except Exception as e:
                        logger.debug(f"选择器 {selector} 失败: {e}")
                        continue

                if pwd_input:
                    pwd_input.fill(password)
                    logger.info("已输入登录密码")
                    time.sleep(0.5)

            try:
                checkbox = self.page.locator("input[type='checkbox']").first
                if checkbox.is_visible(timeout=1000):
                    is_checked = checkbox.is_checked()
                    if not is_checked:
                        checkbox.click()
                        logger.info("已勾选同意条款")
            except Exception:
                pass

            time.sleep(0.5)

            self._click_login_button()
            logger.info("已点击登录按钮")

            time.sleep(3)
            return True

        except Exception as e:
            logger.error(f"自动选择登录用户失败: {e}")
            return False

    def _try_ukey_login(self) -> bool:
        """尝试 UKey 证书登录"""
        logger.info("尝试 UKey 证书登录...")
        try:
            pin_selectors = [
                "input[type='password'][placeholder*='PIN']",
                "input[type='password'][placeholder*='密码']",
                "input[name='pin']",
                "input[id='pin']",
                "input[type='password']",
            ]
            pin_input = None
            for selector in pin_selectors:
                try:
                    element = self.page.locator(selector).first
                    if element.is_visible(timeout=2000):
                        pin_input = element
                        logger.info(f"找到 PIN 输入框: {selector}")
                        break
                except Exception:
                    continue
            if pin_input and self.credentials.pin:
                logger.info("输入 UKey PIN...")
                pin_input.fill(self.credentials.pin)
                time.sleep(1)
                self._click_login_button()
                time.sleep(5)
                return self._check_login_success()
            return False
        except Exception:
            return False

    def _try_password_login(self) -> bool:
        """尝试用户名密码登录"""
        logger.info("尝试用户名密码登录...")
        try:
            user_selectors = [
                "input[name='username']",
                "input[id='username']",
                "input[placeholder*='用户名']",
                "input[type='text']",
            ]
            user_input = None
            for selector in user_selectors:
                try:
                    element = self.page.locator(selector).first
                    if element.is_visible(timeout=2000):
                        user_input = element
                        break
                except Exception:
                    continue
            if user_input and self.credentials.username:
                user_input.fill(self.credentials.username)
                pwd_input = self.page.locator("input[type='password']").first
                if pwd_input and self.credentials.password:
                    pwd_input.fill(self.credentials.password)
                self._click_login_button()
                time.sleep(5)
                return self._check_login_success()
            return False
        except Exception:
            return False

    def _click_login_button(self):
        """点击登录按钮"""
        selectors = [
            "button:has-text('登录')",
            "button:has-text('确定')",
            "input[type='submit']",
            "a:has-text('登录')",
            ".login-btn",
            "#loginBtn",
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

    def _check_login_success(self) -> bool:
        """检查登录是否成功"""
        current_url = self.page.url
        if "/login/" not in current_url and "/main" in current_url:
            logger.info(f"检测到登录成功（URL）: {current_url}")
            return True

        success_selectors = [
            ".user-name",
            ".welcome",
            "#main-frame",
            "text=欢迎",
            ".logout",
            "a:has-text('退出')",
            "text=安全退出",
            "text=欢迎您",
            ".user-info",
            "[class*='user']",
            "[class*='login-out']",
        ]
        for selector in success_selectors:
            try:
                if self.page.locator(selector).first.is_visible(timeout=2000):
                    logger.info(f"检测到登录成功元素: {selector}")
                    return True
            except Exception:
                continue

        logger.info(f"当前仍未确认登录成功，URL: {current_url}")
        return False
