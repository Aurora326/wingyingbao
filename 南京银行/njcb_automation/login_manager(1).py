# -*- coding: utf-8 -*-
"""
登录逻辑模块 - 南京银行适配版
"""
import time
from loguru import logger
from common.keyboard_helper import input_with_aikeyboard, switch_to_english_layout, type_text_raw


class LoginManagerMixin:
    """登录逻辑 Mixin"""

    # 逐位输入：单个字符最多补打几次 / 每次发完等多久确认长度涨了
    _CHAR_MAX_ATTEMPTS = 4
    _CHAR_LAND_TIMEOUT = 2.0

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

            # 第2步：输入网页密码（Ukey登录也必须通过该密码框校验，输错必然登录失败）
            logger.info("[步骤2] 输入网页密码...")
            if not self.credentials.password:
                logger.error("未配置网页密码，无法完成 Ukey 登录")
                return False
            if not self._enter_password():
                logger.error("网页密码输入失败，终止登录（避免用错误密码走完整个 UKey 流程后必然失败）")
                return False

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
        """在 UKey 登录模式下输入网页密码。

        密码框是加密组件：input_value() 只能读到密文（长度 = 实际落入的按键数），
        且无法就地清空（直接改 value 会被站点重新加密回原值，重试会越叠越长，
        实测出现过 20 位）。因此只能按「位数」校验。

        单次输入内部走 _type_password_once 的逐位闭环，偶发丢键会被就地补打；
        只有整串输入失败（位数异常/AiKeyboard 反复失败）才由这里刷新页面重来，
        且必须换一个全新的空输入框，绝不能在同一脏框上重输。
        """
        password = self.credentials.password
        max_attempts = 3

        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                logger.info(f"第 {attempt}/{max_attempts} 次尝试：刷新登录页重置密码框...")
                if not self._reset_login_form():
                    logger.error("刷新登录页失败，无法重置密码框")
                    return False

            pwd_input = self._locate_password_input()
            if not pwd_input:
                logger.warning(f"第 {attempt}/{max_attempts} 次未找到 PIN 输入框")
                continue

            logger.info(f"第 {attempt}/{max_attempts} 次逐位输入密码...")
            if self._type_password_once(pwd_input, password):
                entered = self._read_input_value(pwd_input)
                if self._is_full_length(entered, password):
                    logger.info(f"✓ 密码输入校验通过（{len(password)} 位全部落入）")
                    return True
                logger.warning(f"✗ 逐位确认通过但最终位数不符: 实际 {len(entered) if entered else 0} 位")
                self._log_mismatch_diagnostic(entered, password)

        logger.error(f"网页密码输入 {max_attempts} 次均未完整落入，终止登录")
        return False

    def _locate_password_input(self):
        """查找可见的密码输入框，找不到返回 None。"""
        pin_selectors = [
            "input[type='password']",
            "input[placeholder*='密码']",
            "input[placeholder*='PIN']",
            "input[name='password']",
            "input[name='pin']",
            "input[id='password']",
            "input[id='pin']",
        ]

        for attempt in range(3):
            for selector in pin_selectors:
                try:
                    element = self.page.locator(selector).first
                    if element.count() > 0 and element.is_visible(timeout=2000):
                        logger.info(f"找到 PIN 输入框: {selector}")
                        return element
                except Exception:
                    continue
            time.sleep(2)

        logger.warning("未找到 PIN 输入框")
        return None

    def _type_password_once(self, pwd_input, password: str) -> bool:
        """逐位闭环输入密码：每位都确认真的落入了才推进到下一位。

        背景（实测结论）：AiKeyboard 每输入一个字符会投递 3 个 keydown 事件，
        **在 OS→Chrome 这一段会随机丢掉其中一个**，那个字符就整个丢失（DOM 侧能看到
        事件数从 36 变 35）。丢的位置是随机的（第 12/9/6 位都出现过），与输入法切换、
        按键快慢都无关，放慢也只是缓解。所以"整串打完再数位数"必然经常失败。

        本方法改用闭环：加密框只追加、长度可观测、丢键就不增长，于是
          - 只有当前长度 == 已确认位数时才发这 1 个字符；
          - 发完轮询长度最多 _CHAR_LAND_TIMEOUT 秒等它 +1；
          - 超时仍没涨才重发这一位（重发前会再读一次长度，所以不会打重）。
        因此既不需要重开登录页，也不会出现重复字符。

        不做重试、不清空：整串失败由外层 _enter_password 负责重开登录页。
        """
        # 确保浏览器在前台，再用真实系统鼠标点击获得 OS 级键盘焦点
        try:
            self.page.bring_to_front()
        except Exception:
            pass
        pwd_input.click()
        time.sleep(0.5)
        self._real_click_element(pwd_input)
        time.sleep(0.5)

        # 输入法只切一次：逐位输入期间绝不能重复切换（每次都会弹 Win+Space 浮层，
        # 而且 input_with_aikeyboard 每次还自带 2 秒延时，12 位就是 24 秒）。
        switch_to_english_layout()
        self._real_click_element(pwd_input)
        time.sleep(0.5)

        current = self._current_length(pwd_input)
        if current != 0:
            logger.warning(f"密码框不是空的（已有 {current} 位），放弃本次输入，交由外层重开登录页")
            return False

        for idx, ch in enumerate(password, start=1):
            if not self._type_char_until_landed(pwd_input, ch, idx):
                logger.error(f"✗ 第 {idx} 位字符补打 {self._CHAR_MAX_ATTEMPTS} 次仍未落入")
                self._log_mismatch_diagnostic(self._read_input_value(pwd_input), password)
                return False

        logger.info(f"✓ 密码 {len(password)} 位逐位确认全部落入")
        return True

    def _type_char_until_landed(self, pwd_input, ch: str, target_len: int) -> bool:
        """把第 target_len 位字符打进去，确认长度涨到 target_len 才算成功。

        循环开头先读一次长度：如果上一次的重发其实已经落入、只是读得慢，
        这里会直接看到长度已达标并返回 True，**不会重复发同一个字符**。
        """
        for attempt in range(1, self._CHAR_MAX_ATTEMPTS + 1):
            current = self._current_length(pwd_input)
            if current >= target_len:
                return True
            if current < target_len - 1:
                # 比预期少，说明前面的位丢了、框的状态已不可信 —— 交给外层重开登录页
                logger.warning(f"第 {target_len} 位输入前发现位数异常（当前 {current} 位），放弃本次输入")
                return False

            if not type_text_raw(ch):
                logger.warning(f"第 {target_len} 位第 {attempt}/{self._CHAR_MAX_ATTEMPTS} 次调用 AiKeyboard 失败")
                continue

            if self._wait_for_length(pwd_input, target_len):
                logger.info(f"第 {target_len} 位已落入（第 {attempt} 次尝试）")
                return True
            logger.warning(f"第 {target_len} 位第 {attempt}/{self._CHAR_MAX_ATTEMPTS} 次未落入，重发")
        return False

    def _current_length(self, pwd_input) -> int:
        """当前密码框里的位数；读不到按 0 处理。"""
        val = self._read_input_value(pwd_input)
        return len(val) if val else 0

    def _wait_for_length(self, pwd_input, target_len: int) -> bool:
        """轮询等待密码框长度涨到 target_len（加密组件的赋值有延迟，必须等）。"""
        deadline = time.time() + self._CHAR_LAND_TIMEOUT
        while time.time() < deadline:
            if self._current_length(pwd_input) >= target_len:
                return True
            time.sleep(0.1)
        return False

    def _reset_login_form(self) -> bool:
        """重置密码框：加密组件无法就地清空，只能重新打开登录页换取一个全新的空输入框。"""
        try:
            self.navigate_to_login()
        except Exception as e:
            logger.warning(f"重新打开登录页失败: {e}")
            return False

        self._click_ukey_tab()
        return True

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
