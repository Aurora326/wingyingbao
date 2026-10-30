# -*- coding: utf-8 -*-
"""
登录逻辑模块 - 南京银行适配版
"""
import time
from loguru import logger
from common.keyboard_helper import input_with_aikeyboard


class LoginManagerMixin:
    """登录逻辑 Mixin"""

    def login(self) -> bool:
        """执行登录流程 - 南京银行只走 UKey 登录"""
        logger.info("=" * 50)
        logger.info("开始登录流程...")
        logger.info(f"密码: {'已设置' if self.credentials.password else '未设置'}")

        try:
            logger.info("等待页面加载...")
            time.sleep(3)

            # 第1步：点击 Ukey 登录标签
            logger.info("[步骤1] 点击 Ukey 登录标签...")
            self._click_ukey_tab()

            # 第2步：尝试输入网页密码（Ukey登录可能没有网页密码框）
            logger.info("[步骤2] 尝试输入网页密码...")
            password_entered = False
            if self.credentials.password:
                password_entered = self._enter_password()
                if not password_entered:
                    logger.warning("网页密码输入未完成，Ukey登录模式下可能无需网页密码")
            else:
                logger.warning("未配置密码，跳过网页密码输入")

            # 第3步：点击登录按钮
            logger.info("[步骤3] 点击登录按钮...")
            self._click_login_button()

            # 第4步：处理验证码弹窗（如果启用）
            try:
                from common.captcha_solver import CaptchaSolver
                if hasattr(self, 'captcha_config') and self.captcha_config and self.captcha_config.enabled:
                    solver = CaptchaSolver(
                        self.page,
                        api_key=self.captcha_config.api_key,
                        secret_key=self.captcha_config.secret_key,
                        enabled=self.captcha_config.enabled,
                    )
                    solver.solve_if_present(
                        dialog_selector=self.captcha_config.dialog_selector,
                        input_selector=self.captcha_config.input_selector,
                        img_selector=self.captcha_config.img_selector,
                        submit_selector=self.captcha_config.submit_selector,
                    )
            except Exception as e:
                logger.warning(f"验证码处理异常: {e}")

            # 第5步：输入 USB Key 密码（弹窗已自动激活输入框）
            pin = self.credentials.pin
            if pin:
                logger.info("等待 USB Key 密码弹窗...")
                time.sleep(2)
                logger.info("输入 USB Key PIN...")
                try:
                    from common.keyboard_helper import click_native_button, input_with_aikeyboard, send_with_aikeyboard
                    if input_with_aikeyboard(pin):
                        time.sleep(0.5)
                        enter_sent = send_with_aikeyboard("Enter")
                        time.sleep(0.5)
                        clicked = click_native_button(timeout=3)
                        logger.info(f"USB Key PIN 提交结果: enter_sent={enter_sent}, native_button_clicked={clicked}")
                        logger.info("USB Key PIN 已输入并提交")
                    else:
                        logger.warning("AiKeyboard 输入 PIN 失败")
                except Exception as e:
                    logger.error(f"输入 USB Key PIN 失败: {e}")
                time.sleep(2)

            # 第6步：如果有UKey确认，机柜点OK
            if self.cabinet_controller:
                logger.info("等待 USBKey 屏幕显示交易信息...")
                time.sleep(3)
                logger.info("使用机柜点击物理 USBKey OK键...")
                self.cabinet_controller.click_ok()
                time.sleep(3)

            # 第6步：等待登录结果
            logger.info("等待登录结果...")
            time.sleep(5)

            start = time.time()
            while time.time() - start < 20:
                if self._check_login_success():
                    logger.info("✓ 登录成功！")
                    return True
                time.sleep(1)

            logger.warning("登录检测超时，可能还在处理中")
            return False

        except Exception as e:
            logger.error(f"登录异常: {e}")
            return False

    def _click_ukey_tab(self):
        """点击 Ukey 登录标签"""
        time.sleep(3)  # 等页面完全渲染

        # 方法1：用户提供的精确选择器
        try:
            tab = self.page.locator("#RT_login > div > div.loginAreaBlock > div > div > div.btnGroupSwitch > div.basicStyle.leftBorderRadius").first
            if tab.count() > 0 and tab.is_visible(timeout=5000):
                tab.click()
                logger.info("已点击 Ukey 登录标签 (精确选择器)")
                time.sleep(2)
                return
        except Exception as e:
            logger.warning(f"精确选择器失败: {e}")

        # 方法2：get_by_role + name
        try:
            tab = self.page.get_by_role("button", name="Ukey登录").first
            if tab.count() > 0 and tab.is_visible(timeout=5000):
                tab.click()
                logger.info("已点击 Ukey 登录标签 (get_by_role)")
                time.sleep(2)
                return
        except Exception as e:
            logger.warning(f"get_by_role 失败: {e}")

        # 方法3：JS 兜底 - 模拟真实点击事件
        logger.warning("选择器都未命中，尝试 JS 兜底点击...")
        try:
            result = self.page.evaluate("""() => {
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
                let node;
                while (node = walker.nextNode()) {
                    const txt = node.textContent.trim();
                    if (txt === 'Ukey登录' || txt === 'UKey登录') {
                        const el = node.parentElement;
                        // 模拟真实点击事件序列
                        el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                        el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                        el.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                        return {found: true, text: txt, tag: el.tagName};
                    }
                }
                return {found: false};
            }""")
            logger.info(f"JS 兜底点击结果: {result}")
            time.sleep(2)
        except Exception as e:
            logger.error(f"JS 兜底点击失败: {e}")

    def _enter_password(self) -> bool:
        """在 UKey 登录模式下输入密码"""
        password = self.credentials.password

        # 查找 PIN 输入框
        pin_selectors = [
            "input[type='password']",
            "input[placeholder*='密码']",
            "input[placeholder*='PIN']",
            "input[name='password']",
            "input[name='pin']",
            "input[id='password']",
            "input[id='pin']",
        ]

        pwd_input = None
        found_selector = None

        for attempt in range(3):
            logger.info(f"第 {attempt + 1} 次尝试查找 PIN 输入框...")

            for selector in pin_selectors:
                try:
                    element = self.page.locator(selector).first
                    if element.count() > 0 and element.is_visible(timeout=2000):
                        pwd_input = element
                        found_selector = selector
                        logger.info(f"找到 PIN 输入框: {selector}")
                        break
                except Exception:
                    continue

            if pwd_input:
                break
            time.sleep(2)

        if not pwd_input:
            logger.warning("未找到 PIN 输入框")
            return False

        # 确保浏览器在前台，先用 Playwright 点击定位，再用真实系统鼠标点击获得 OS 级键盘焦点
        try:
            self.page.bring_to_front()
        except Exception:
            pass
        pwd_input.click()
        time.sleep(0.5)
        self._real_click_element(pwd_input)
        time.sleep(1)

        # 使用 AiKeyboard 输入密码，输入后校验位数是否完整，必要时重试。
        # 注意：南京银行密码框是加密输入组件，input_value() 返回密文而非明文，
        # 因此只能按"位数"校验（12 位 = 12 次真实按键全部落入），不能比较明文内容。
        max_attempts = 3
        entered_ok = False
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                # 重试前清空输入框，避免残留字符叠加
                self._clear_input_field(pwd_input)
                time.sleep(0.3)

            logger.info(f"第 {attempt}/{max_attempts} 次使用 AiKeyboard 输入密码...")
            if not input_with_aikeyboard(password):
                logger.warning(f"AiKeyboard 输入失败（第 {attempt} 次）")
                continue

            time.sleep(0.5)
            entered = self._read_input_value(pwd_input)
            if self._is_full_length(entered, password):
                logger.info(f"✓ 密码输入校验通过（{len(password)} 位按键全部落入）")
                entered_ok = True
                break

            logger.warning(f"✗ 密码输入位数不足: 期望 {len(password)} 位, 实际 {len(entered) if entered else 0} 位")
            self._log_mismatch_diagnostic(entered, password)

        time.sleep(0.5)
        return entered_ok

    def _is_full_length(self, entered, password) -> bool:
        """校验输入框位数是否完整（密码框为加密组件时只能按位数判断，不能比较明文）。
        读取失败视为不完整。"""
        return entered is not None and len(entered) == len(password)

    def _log_mismatch_diagnostic(self, entered, password) -> None:
        """记录输入位数不足时的位置特征，用于定位（不泄露密码明文）。
        注意：若密码框是加密组件，密文与明文前缀/尾部匹配为 0 属正常现象。"""
        if not entered:
            logger.warning("诊断: 密码框为空")
            return
        n = min(len(entered), len(password))
        prefix = next((i for i in range(n) if entered[i] != password[i]), n)
        tail = sum(1 for a, b in zip(reversed(entered), reversed(password)) if a == b)
        logger.info(f"诊断: 前缀匹配 {prefix} 位 / 尾部匹配 {tail} 位（输入 {len(entered)} 位, 期望 {len(password)} 位；密文场景下匹配 0 属正常）")

    def _read_input_value(self, element) -> str:
        """读取输入框当前值（password 输入框同样适用）。读取失败返回 None。"""
        try:
            return element.input_value()
        except Exception as e:
            logger.debug(f"读取输入框值失败: {e}")
            return None

    def _clear_input_field(self, element) -> bool:
        """可靠清空输入框，返回是否已清空。密码框为加密组件时，真实键盘删除最可靠
        （直接改 value 可能被站点重新加密回原值）。"""
        if self._is_empty(element):
            return True
        # 方法1：真实系统键盘 Ctrl+A + Backspace（输入框已获 OS 焦点，事件最接近真人）
        try:
            import pyautogui
            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.2)
            pyautogui.press('backspace')
            time.sleep(0.5)
            if self._is_empty(element):
                logger.debug("pyautogui 键盘清空成功")
                return True
        except Exception as e:
            logger.debug(f"pyautogui 键盘清空失败: {e}")
        # 方法2：Playwright 页面键盘 Ctrl+A + Backspace
        try:
            element.click()
            time.sleep(0.2)
            self.page.keyboard.press("Control+A")
            self.page.keyboard.press("Backspace")
            time.sleep(0.5)
            if self._is_empty(element):
                logger.debug("page.keyboard 清空成功")
                return True
        except Exception as e:
            logger.debug(f"page.keyboard 清空失败: {e}")
        # 方法3：JS 原生 value setter + 触发 input 事件（受控组件清空）
        try:
            element.evaluate("""(el) => {
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                setter.call(el, '');
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            }""")
            time.sleep(0.3)
            if self._is_empty(element):
                logger.debug("JS setter 清空成功")
                return True
        except Exception as e:
            logger.debug(f"JS setter 清空失败: {e}")
        return False

    def _is_empty(self, element) -> bool:
        """判断输入框是否为空（读取失败视为非空，避免误判已清空）。"""
        try:
            return element.input_value() == ""
        except Exception:
            return False

    def _real_click_element(self, element) -> bool:
        """用真实系统鼠标点击元素中心，确保 OS 级键盘焦点。
        Playwright 的 click 是 CDP 合成事件，部分站点/浏览器不会把随后的真实按键
        完整交给该元素（表现为输入丢失首位字符）。"""
        try:
            box = element.bounding_box()
            if not box:
                logger.debug("无法获取元素位置，跳过真实点击")
                return False
            screen = self.page.evaluate(
                """(rect) => {
                    const cx = rect.x + rect.width / 2;
                    const cy = rect.y + rect.height / 2;
                    return {
                        x: Math.round(window.screenX + (window.outerWidth - window.innerWidth) / 2 + cx),
                        y: Math.round(window.screenY + (window.outerHeight - window.innerHeight) + cy),
                    };
                }""",
                box,
            )
            import pyautogui
            pyautogui.click(screen["x"], screen["y"])
            logger.info(f"真实点击输入框: ({screen['x']}, {screen['y']})")
            return True
        except Exception as e:
            logger.debug(f"真实点击失败: {e}")
            return False

    def _click_login_button(self):
        """点击登录按钮"""
        selectors = [
            "#RT_login > div > div.loginAreaBlock > div > div > div.form-box > div.submit",
            "button:has-text('登录')",
            "button:has-text('登 录')",
            "button[type='submit']",
            ".login-btn",
            "#loginBtn",
            ".ant-btn-primary",
            ".el-button--primary",
        ]
        for selector in selectors:
            try:
                btn = self.page.locator(selector).first
                if btn.count() > 0 and btn.is_visible(timeout=2000):
                    logger.info(f"点击登录按钮: {selector}")
                    btn.click()
                    return
            except Exception:
                continue

        logger.warning("未找到登录按钮")

    def _check_login_success(self) -> bool:
        """检查登录是否成功"""
        success_selectors = [
            ".user-name",
            ".welcome",
            ".username",
            "text=欢迎",
            "text=首页",
            ".logout",
            "a:has-text('退出')",
            ".user-info",
            ".account-info",
            "text=工作台",
            "text=我的账户",
        ]
        for selector in success_selectors:
            try:
                if self.page.locator(selector).first.is_visible(timeout=2000):
                    logger.info(f"检测到登录成功元素: {selector}")
                    return True
            except Exception:
                continue

        current_url = self.page.url
        if "login" not in current_url.lower() and ("ntms" in current_url.lower() or "njcb" in current_url.lower()):
            logger.info(f"检测到 URL 已变化，可能已登录: {current_url}")
            return True

        return False
