# -*- coding: utf-8 -*-
"""
登录逻辑模块 - 建设银行适配版

核心策略：
1. 在点击"立即登录"之前，先用 Playwright 获取按钮的页面坐标（相对于视口）
2. 点击"立即登录"时使用 Playwright 的 page.mouse.click()（精确，直接操作浏览器内部坐标）
3. 点击之后浏览器弹出证书模态对话框，Playwright 被阻塞，此时只用 pyautogui 处理
4. 处理完证书对话框和 PIN 码弹窗，登录完成后再恢复使用 Playwright
"""
import time
from logger import setup_logger

logger = setup_logger()


class LoginManagerMixin:
    """登录逻辑 Mixin"""

    def _find_password_input_context(self):
        """在所有页面和 iframe 中查找可能的密码输入控件。"""
        password_selectors = [
            '#LOG_PWD',
            'input[type="password"]',
            'input[id*="PWD" i]',
            'input[name*="PWD" i]',
            'input[id*="PASS" i]',
            'input[name*="PASS" i]',
            'input[id*="PSW" i]',
            'input[name*="PSW" i]',
            'input[class*="pwd" i]',
            'input[class*="pass" i]',
            'input[placeholder*="密码"]',
            'input[title*="密码"]',
            '.password input',
            '.pwd input',
            '.login_password input',
            '.login-pwd input',
        ]

        js_code = """
        (selectors) => {
            for (const selector of selectors) {
                const el = document.querySelector(selector);
                if (!el) continue;
                const rect = el.getBoundingClientRect();
                return {
                    found: true,
                    selector,
                    id: el.id || '',
                    name: el.name || '',
                    type: el.type || '',
                    placeholder: el.placeholder || '',
                    className: el.className || '',
                    visible: !!(rect.width && rect.height),
                    totalPwdInputs: document.querySelectorAll('input[type="password"]').length
                };
            }

            const candidates = Array.from(document.querySelectorAll('input')).map((el) => ({
                id: el.id || '',
                name: el.name || '',
                type: el.type || '',
                placeholder: el.placeholder || '',
                title: el.title || '',
                className: el.className || '',
                autocomplete: el.autocomplete || ''
            }));

            const fuzzy = candidates.find((item) => {
                const text = [
                    item.id,
                    item.name,
                    item.type,
                    item.placeholder,
                    item.title,
                    item.className,
                    item.autocomplete
                ].join(' ').toLowerCase();
                return (
                    text.includes('pwd') ||
                    text.includes('pass') ||
                    text.includes('psw') ||
                    text.includes('密码')
                );
            });

            if (fuzzy) {
                let selector = '';
                if (fuzzy.id) selector = 'input[id="' + fuzzy.id + '"]';
                else if (fuzzy.name) selector = 'input[name="' + fuzzy.name + '"]';
                else if (fuzzy.type === 'password') selector = 'input[type="password"]';
                if (selector) {
                    return {
                        found: true,
                        selector,
                        id: fuzzy.id,
                        name: fuzzy.name,
                        type: fuzzy.type,
                        placeholder: fuzzy.placeholder,
                        className: fuzzy.className,
                        totalPwdInputs: document.querySelectorAll('input[type="password"]').length
                    };
                }
            }

            return {
                found: false,
                totalPwdInputs: document.querySelectorAll('input[type="password"]').length,
                candidateCount: candidates.length,
                fuzzyCandidate: fuzzy || null
            };
        }
        """

        for i, page in enumerate(self.page.context.pages):
            contexts = [(page, f"page {i}")]
            for j, frame in enumerate(page.frames):
                if frame == page.main_frame:
                    continue
                contexts.append((frame, f"page {i} frame {j}"))

            for context, label in contexts:
                try:
                    url = context.url
                    js_result = context.evaluate(js_code, password_selectors)
                    logger.info(f"{label} ({url}): 密码框检测结果: {js_result}")
                    if js_result.get('found'):
                        logger.info(f"找到包含密码输入框的上下文: {label} ({url})")
                        return page, context, js_result
                except Exception as e:
                    logger.debug(f"检查 {label} 时出错: {e}")

        return None, None, None

    def _fill_password_input(self, context, selector: str, password: str) -> bool:
        """优先用 Playwright 填充，失败时回退到原生 JS 赋值。"""
        try:
            pwd_input = context.locator(selector).first
            pwd_input.wait_for(state="attached", timeout=3000)
            try:
                pwd_input.fill(password, timeout=3000)
            except Exception:
                pwd_input.click(timeout=2000)
                pwd_input.press_sequentially(password, delay=80)
            return True
        except Exception as e:
            logger.warning(f"Playwright 填充密码框失败，尝试 JS 回退: {e}")

        try:
            success = context.evaluate(
                """({selector, password}) => {
                    const el = document.querySelector(selector);
                    if (!el) return false;
                    el.removeAttribute('readonly');
                    el.removeAttribute('disabled');
                    el.focus();
                    el.value = password;
                    ['input', 'change', 'blur'].forEach((eventType) => {
                        el.dispatchEvent(new Event(eventType, { bubbles: true }));
                    });
                    return true;
                }""",
                {"selector": selector, "password": password},
            )
            return bool(success)
        except Exception as e:
            logger.warning(f"JS 回填密码框失败: {e}")
            return False

    def _find_login_button_in_context(self, context):
        """查找中间登录页的登录按钮。"""
        button_selectors = [
            '#LOGIN_BOX_BTN',
            'input#LOGIN_BOX_BTN',
            'input[name="next"]',
            'input.pbd_btn_login',
            'input.btn_js_link',
            'input[type="button"][value="登 录"]',
            'input[type="button"][value="登录"]',
            'input[type="submit"][value="登录"]',
            'input[type="submit"][value="登 录"]',
            'button:has-text("登录")',
            'button:has-text("登 录")',
            'a:has-text("登录")',
            'a:has-text("登 录")',
            '.login_btn',
            '#loginBtn',
            '.btn-login',
        ]

        for selector in button_selectors:
            try:
                loc = context.locator(selector).first
                if loc.count() == 0:
                    continue
                logger.info(f"找到登录按钮选择器: {selector}")
                return loc, selector
            except Exception as e:
                logger.debug(f"检查登录按钮选择器失败 {selector}: {e}")

        return None, None

    def _click_login_button(self, context) -> bool:
        """点击中间登录页的登录按钮，失败时回退到 JS click。"""
        login_btn, selector = self._find_login_button_in_context(context)
        if not login_btn or not selector:
            logger.warning("未找到登录按钮")
            return False

        try:
            login_btn.wait_for(state="visible", timeout=3000)
        except Exception:
            pass

        try:
            login_btn.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass

        try:
            login_btn.click(timeout=5000)
            logger.info(f"已点击登录按钮: {selector}")
            return True
        except Exception as e:
            logger.warning(f"Playwright 点击登录按钮失败，尝试 JS 回退: {e}")

        try:
            success = context.evaluate(
                """(selector) => {
                    const el = document.querySelector(selector);
                    if (!el) return false;
                    el.focus();
                    el.click();
                    return true;
                }""",
                selector,
            )
            if success:
                logger.info(f"JS 已点击登录按钮: {selector}")
                return True
        except Exception as e:
            logger.warning(f"JS 点击登录按钮失败: {e}")

        return False

    def login(self) -> bool:
        """
        执行自动登录（建设银行登录页）
        
        建设银行企业网银登录流程：
        1. 页面默认显示"证书登录"标签
        2. 点击"立即登录"按钮 → 弹出证书选择对话框
        3. 确认证书 → 弹出网银盾 PIN 码窗口
        4. 输入 PIN 码 → 登录成功
        """
        logger.info("=" * 50)
        logger.info("开始登录流程（建设银行）...")
        
        try:
            # 等待页面加载
            logger.info("等待页面加载...")
            time.sleep(5)

            # 新版证书登录页直接展示密码表单，没有“立即登录”按钮
            page, context, meta = self._find_password_input_context()
            if page is not None:
                logger.info("检测到证书登录表单，直接填写登录密码...")
                if self._submit_login_password_form():
                    self._handle_auth_popups(timeout=60)
                    self._adopt_portal_page(timeout=20)
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=30000)
                    except Exception:
                        pass
                    return self._check_login_success()

            # 检查当前是否是证书登录模式
            self._ensure_certificate_login_tab()
            
            # ===== 关键：用 Playwright 精确点击"立即登录"按钮 =====
            # Playwright 的 page.mouse.click() 直接操作浏览器内部坐标，
            # 不受窗口偏移影响，比 pyautogui 精确得多。
            logger.info("获取登录按钮页面坐标...")
            btn_pos = self._get_login_button_pos()
            
            if not btn_pos:
                logger.error("未能获取登录按钮坐标")
                return False
            
            # 有效性检查：确保按钮在页面右侧（避免点中左侧广告）
            viewport = self.page.viewport_size or {'width': 1920, 'height': 1080}
            if btn_pos['x'] < viewport['width'] * 0.4:
                logger.warning(f"按钮 x 坐标 {btn_pos['x']} 偏左，可能在广告区域")
                return False
            
            # 使用 Playwright mouse.click() 精确点击
            logger.info(f"Playwright 点击立即登录按钮: ({btn_pos['x']}, {btn_pos['y']})")
            try:
                self.page.bring_to_front()
            except Exception:
                pass
            self.page.mouse.click(btn_pos['x'], btn_pos['y'])
            
            # ===== 点击后不再使用 Playwright API！只用 pyautogui =====
            # 等待证书选择对话框出现
            logger.info("等待证书选择对话框...")
            time.sleep(3)
            
            # 处理证书选择对话框
            self._handle_certificate_dialog()
            
            # 处理 PIN 码弹窗
            self._handle_pin_dialog()
            
            # 等待页面跳转
            logger.info("等待页面跳转...")
            time.sleep(10)
            
            # ===== 先关闭所有弹窗，确保页面干净 =====
            logger.info("清理弹窗...")
            self._close_blank_pages()
            time.sleep(2)
            
            # 确保回到主页面（弹窗关闭后可能焦点不在主页面）
            try:
                self.page.bring_to_front()
                time.sleep(1)
            except Exception:
                pass
            
            # ===== 处理中间登录页（证书+密码二次验证）=====
            # PIN 码验证后，建行可能跳转到需要输入登录密码的中间页
            if self._handle_intermediate_login_page():
                logger.info("中间登录页处理完成")
            
            # 检查是否登录成功
            if self._check_login_success():
                logger.info("登录成功")
                # 关闭可能已打开的弹窗页
                self._close_blank_pages()
                return True
            else:
                logger.warning("登录可能未完成，请检查")
                return False
                
        except Exception as e:
            logger.error(f"登录过程出错: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    def _get_login_button_pos(self):
        """
        获取'立即登录'按钮的页面坐标（相对于视口）
        
        使用 Playwright 原生 locator API 获取，比 JS 的 getBoundingClientRect() 更可靠。
        返回页面坐标，后续使用 Playwright 的 page.mouse.click() 点击。
        """
        selectors = [
            'input[onclick*="toCTLoginJsp"]',
            'input.pbd_btn_login',
            'input[value="立即登录"]',
            'input[type="button"][value="立即登录"]',
            'button:has-text("立即登录")',
            'a:has-text("立即登录")',
            '.login_div input[type="button"]',
            '.login_w input[type="button"]',
        ]
        
        for selector in selectors:
            try:
                loc = self.page.locator(selector).first
                if loc.count() == 0:
                    continue
                
                # 检查可见性
                try:
                    is_visible = loc.is_visible(timeout=500)
                except Exception:
                    is_visible = False
                
                # 获取边界框
                try:
                    box = loc.bounding_box(timeout=1000)
                except Exception:
                    box = None
                
                logger.info(
                    f"选择器 '{selector}': count={loc.count()}, "
                    f"visible={is_visible}, box={box}"
                )
                
                if box and box['width'] > 0 and box['height'] > 0:
                    x = round(box['x'] + box['width'] / 2)
                    y = round(box['y'] + box['height'] / 2)
                    logger.info(
                        f"按钮页面坐标: ({x}, {y}), "
                        f"尺寸: {box['width']:.0f}x{box['height']:.0f}"
                    )
                    return {'x': x, 'y': y, 'width': box['width'], 'height': box['height']}
                    
            except Exception as e:
                logger.debug(f"选择器 '{selector}' 出错: {e}")
                continue
        
        logger.warning("所有选择器均未找到有效的登录按钮")
        return None
    
    def _close_blank_pages(self):
        """关闭所有弹窗页（空白页、公告页等非主页面）"""
        try:
            current_page = self.page
            for page in self.page.context.pages:
                try:
                    url = page.url
                    if page == current_page:
                        continue
                    if (url == "about:blank" or url == "" or "blank" in url or
                        "article" in url or "notice" in url or "announce" in url):
                        logger.info(f"关闭弹窗页: {url}")
                        page.close()
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"关闭弹窗页时出错: {e}")
    
    def _iter_contexts(self, page):
        """页面自身和它的 iframe：新版建行把登录表单和门户都放在 iframe 里。"""
        yield page
        try:
            frames = list(page.frames)
        except Exception:
            return
        for frame in frames:
            try:
                if frame == page.main_frame:
                    continue
                yield frame
            except Exception:
                continue

    def _page_has_portal(self, page) -> bool:
        """判断页面是否已经进入网银门户（含 iframe，兼容新版首页）。"""
        script = """() => {
            const t = document.body ? document.body.innerText : '';
            const hasNode = !!document.querySelector('#toAccQuery, .menu-nav, .main-nav, .headBtn_a.workEplatform');
            const hasNavText = t.includes('账户查询') && (t.includes('退出') || t.includes('您好'));
            return hasNode || hasNavText;
        }"""
        for ctx in self._iter_contexts(page):
            try:
                if ctx.evaluate(script):
                    return True
            except Exception:
                continue
        return False

    def _adopt_portal_page(self, timeout: float = 20.0) -> bool:
        """登录结果可能开在新窗口里，找到带门户元素的页面并切换过去。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                pages = list(self.page.context.pages)
            except Exception:
                return False
            for candidate in pages:
                try:
                    if self._page_has_portal(candidate):
                        self.page = candidate
                        try:
                            candidate.bring_to_front()
                        except Exception:
                            pass
                        logger.info(f"切换到登录后的门户页面: {candidate.url}")
                        return True
                except Exception:
                    continue
            time.sleep(1)
        return False

    def _submit_login_password_form(self) -> bool:
        """填写证书登录表单的登录密码并提交。

        建行证书登录页需要的顺序是：证书 + U盾 PIN + 登录密码。
        提交按钮会再次触发 U盾 PIN，所以这里用 JS 点击，不等页面跳转。
        """
        page, context, meta = self._find_password_input_context()
        if page is None or context is None or not meta:
            return False

        password = self.credentials.password if self.credentials else None
        if not password:
            logger.warning("未配置登录密码，等待手动输入...")
            time.sleep(20)
            return False

        self.page = page
        pwd_selector = meta.get("selector") or '#LOG_PWD, input[type="password"]'
        logger.info(f"填写登录密码，selector={pwd_selector}")
        if not self._fill_password_input(context, pwd_selector, password):
            logger.warning("未能填写登录密码")
            return False

        login_btn, selector = self._find_login_button_in_context(context)
        if not selector:
            logger.warning("未找到登录按钮")
            return False

        try:
            self.page.set_default_timeout(3000)
            context.evaluate(
                "(selector) => { const el = document.querySelector(selector); if (el) { el.focus(); el.click(); } }",
                selector,
            )
            logger.info(f"已点击登录按钮: {selector}")
        except Exception as e:
            # 点击后 U盾 PIN 弹窗会阻塞页面，evaluate 超时属于正常情况
            logger.info(f"登录按钮已点击（页面进入等待: {type(e).__name__}）")
        return True

    def _handle_intermediate_login_page(self) -> bool:
        """
        处理证书登录后的中间登录页
        
        PIN 码验证通过后，建行可能跳转到需要输入登录密码的页面。
        由于弹窗干扰，self.page 可能不是正确的页面，需要遍历所有页面查找。
        """
        try:
            # 弹窗关闭后页面可能还没恢复，等一等再检测
            time.sleep(3)
            
            target_page, target_context, pwd_meta = self._find_password_input_context()

            if not target_page or not target_context or not pwd_meta:
                logger.info("未在任何页面检测到密码输入框，跳过中间登录页处理")
                return False
            
            # 确保目标页面在前台
            try:
                target_page.bring_to_front()
                time.sleep(1)
            except Exception:
                pass
            
            # 获取登录密码
            password = self.credentials.password if self.credentials else None
            if not password:
                logger.warning("未配置登录密码，等待手动输入...")
                time.sleep(20)
                return False
            
            # 在目标页面上输入密码
            logger.info("输入登录密码...")
            pwd_selector = pwd_meta.get("selector") or '#LOG_PWD, input[type="password"]'
            if not self._fill_password_input(target_context, pwd_selector, password):
                logger.warning(f"未能自动填充密码框，selector={pwd_selector}")
                return False
            time.sleep(1)
            
            # 点击登录按钮
            logger.info("点击登录按钮...")
            if self._click_login_button(target_context):
                time.sleep(5)
                # 更新 self.page 指向登录后的页面
                self.page = target_page
                return True
            return False
                
        except Exception as e:
            logger.debug(f"中间登录页处理出错: {e}")
            return False
    
    def _ensure_certificate_login_tab(self):
        """确保当前选中的是'证书登录'标签"""
        try:
            cert_tab = self.page.locator(
                '.login_tab:has-text("证书登录"), .tab:has-text("证书登录"), [data-tab="cert"]'
            ).first
            if cert_tab.count() > 0:
                is_active = cert_tab.evaluate(
                    "el => el.classList.contains('active') || el.classList.contains('on')"
                )
                if not is_active:
                    logger.info("切换到证书登录标签...")
                    cert_tab.click()
                    time.sleep(1)
                else:
                    logger.info("当前已在证书登录标签")
            else:
                logger.info("未检测到证书登录标签切换器，可能默认就是证书登录")
        except Exception as e:
            logger.debug(f"检查证书登录标签时出错: {e}")
    
    def _handle_certificate_dialog(self):
        """处理证书选择对话框（Windows 原生窗口，全程走 Win32，避免 pywinauto 卡住）。"""
        logger.info("处理证书选择对话框...")
        titles = ("选择证书", "证书")

        try:
            from common.windows_ui import double_click_in_dialog, fast_click_button, find_hwnd
        except ImportError:
            logger.warning("无法导入窗口工具，跳过证书弹窗处理")
            return

        if not find_hwnd(titles):
            logger.info("未检测到证书弹窗，跳过")
            return

        # 全程纯 Win32：模态证书弹窗下 UIA/pywinauto 会阻塞
        if double_click_in_dialog(titles, 0.57, 0.36, timeout=2):
            logger.info("已双击证书行")
        if fast_click_button(titles, ("确定", "确认", "OK"), timeout=2):
            logger.info("已点击证书弹窗的「确定」")
        elif not self._force_cert_dialog_ok(titles):
            logger.warning("未点击到证书弹窗的「确定」")

        time.sleep(1)
        logger.info("证书选择对话框处理完成")

    def _force_cert_dialog_ok(self, titles) -> bool:
        """兜底确认证书弹窗：按文字点按钮 / 回车 / IDOK。"""
        import ctypes
        import re as _re

        from common.windows_ui import _child_class, _enum_children, _window_text, find_hwnd

        hwnd = find_hwnd(titles)
        if not hwnd:
            logger.info("证书弹窗已关闭，无需确认")
            return True
        user32 = ctypes.windll.user32
        children = list(_enum_children(hwnd))
        # 1) 枚举按钮，兼容“确 定”“&确定”等文字
        for child in children:
            text = _re.sub(r"[\s&]", "", _window_text(child) or "")
            if _child_class(child).lower() != "button":
                continue
            if text in ("确定", "确认", "OK", "是", "继续") or "确" in text:
                user32.SendMessageW(child, 0x00F5, 0, 0)  # BM_CLICK
                logger.info(f"已点击证书弹窗按钮：{_window_text(child)}")
                return True
        # 2) 回车触发默认按钮
        try:
            user32.SetForegroundWindow(hwnd)
            user32.PostMessageW(hwnd, 0x0100, 0x0D, 0)  # WM_KEYDOWN VK_RETURN
            user32.PostMessageW(hwnd, 0x0101, 0x0D, 0)  # WM_KEYUP
            logger.info("已向证书弹窗发送回车")
            return True
        except Exception:
            pass
        # 3) WM_COMMAND IDOK
        try:
            user32.SendMessageW(hwnd, 0x0111, 1, 0)
            logger.info("已向证书弹窗发送 IDOK")
            return True
        except Exception:
            pass
        detail = [(_child_class(c), (_window_text(c) or "").strip()) for c in children]
        logger.warning(f"证书弹窗子控件: {detail[:20]}")
        return False
    
    def _check_login_success(self) -> bool:
        """检查登录是否成功 - 建设银行适配"""
        try:
            try:
                if hasattr(self, "_close_auxiliary_pages"):
                    self._close_auxiliary_pages()
            except Exception as e:
                logger.debug(f"登录检查前关闭辅助页面失败: {e}")

            pages = []
            try:
                pages = list(self.page.context.pages)
            except Exception:
                pages = [self.page]

            for i, page in enumerate(pages):
                try:
                    current_url = page.url
                    logger.info(f"检查登录状态页面 {i}: {current_url}")

                    url_lower = current_url.lower()
                    if (
                        hasattr(self, "_is_auxiliary_popup_url") and
                        self._is_auxiliary_popup_url(current_url)
                    ):
                        logger.info(f"登录检查时关闭辅助页面 {i}: {current_url}")
                        try:
                            if page != self.page:
                                page.close()
                            else:
                                other_pages = [
                                    p for p in pages
                                    if p != page and "b2b.ccb.com" in (p.url or "").lower()
                                ]
                                if other_pages:
                                    self.page = other_pages[0]
                                    try:
                                        self.page.bring_to_front()
                                    except Exception:
                                        pass
                                    page.close()
                        except Exception as e:
                            logger.warning(f"关闭辅助页面失败: {current_url}, error={e}")
                        continue

                    login_url_markers = [
                        "b2bindex.jsp",
                        "index.jsp",
                        "mnecv6b2bmainplat_un",
                        "txcode=n7n010",
                        "login_type=uc",
                    ]
                    is_login_url = any(marker in url_lower for marker in login_url_markers)

                    login_page_selectors = [
                        '#LOGIN_BOX_BTN',
                        '#LOG_PWD',
                        '.login_tab',
                        'text=证书登录',
                        'text=手机/用户名登录',
                        'input.pbd_btn_login',
                    ]
                    visible_login_markers = []
                    is_login_page = is_login_url
                    for selector in login_page_selectors:
                        try:
                            loc = page.locator(selector).first
                            if loc.count() == 0:
                                continue
                            try:
                                is_visible = loc.is_visible(timeout=1000)
                            except Exception:
                                is_visible = False
                            if is_visible:
                                visible_login_markers.append(selector)
                        except Exception:
                            continue

                    if visible_login_markers:
                        is_login_page = True

                    # ===== 登录成功判断 =====
                    # 检查密码框（有说明还在登录页），登录表单在 iframe 里也要算
                    pwd_count = 0
                    pwd_visible = False
                    for ctx in self._iter_contexts(page):
                        try:
                            pwd_loc = ctx.locator('#LOG_PWD, input[type="password"]').first
                            cnt = pwd_loc.count()
                        except Exception:
                            continue
                        if not cnt:
                            continue
                        pwd_count += cnt
                        try:
                            if pwd_loc.is_visible(timeout=500):
                                pwd_visible = True
                        except Exception:
                            pass
                    logger.info(f"页面 {i} 密码框 count={pwd_count}, visible={pwd_visible}")
                    if pwd_visible:
                        logger.info(f"页面 {i} 仍有可见密码框，判定为登录页")
                        continue
                    
                    # 检查登录成功标志（退出按钮、工作台、菜单栏）
                    success_checks = [
                        (':has-text("退出")', '退出按钮'),
                        (':has-text("安全退出")', '安全退出按钮'),
                        ('.headBtn_a.workEplatform', '工作台链接'),
                        (':has-text("工作台")', '工作台文字'),
                        ('.menu-nav', '菜单栏'),
                        ('.main-nav', '主导航'),
                        ('a:has-text("退出")', 'a标签退出'),
                    ]
                    for sel, desc in success_checks:
                        try:
                            loc = page.locator(sel).first
                            cnt = loc.count()
                            logger.info(f"页面 {i} 检查 {desc}: count={cnt}, selector={sel}")
                            if cnt > 0:
                                try:
                                    vis = loc.is_visible(timeout=1000)
                                    logger.info(f"页面 {i} 检查 {desc}: visible={vis}")
                                    if vis:
                                        logger.info(f"检测到登录成功（{desc}: {sel}），页面 {i}, url={current_url}")
                                        self.page = page
                                        return True
                                except Exception as e:
                                    logger.info(f"页面 {i} 检查 {desc} is_visible 异常: {e}")
                        except Exception as e:
                            logger.info(f"页面 {i} 检查 {desc} 异常: {e}")
                    
                    # 备用：按页面可见的导航文字判断（兼容新版首页）
                    if self._page_has_portal(page):
                        logger.info(f"检测到登录成功（门户导航已出现），页面 {i}, url={current_url}")
                        self.page = page
                        return True

                    # 最终判定：b2bindex.jsp 没有密码框还不够，必须确认已进入门户
                    if 'b2bindex.jsp' in current_url.lower() and pwd_count == 0:
                        try:
                            page_title = page.title() or ""
                        except Exception:
                            page_title = ""
                        if "No required SSL certificate" in page_title or "400 Bad Request" in page_title:
                            logger.error("页面提示未发送客户端证书：U盾未被浏览器识别")
                            continue
                        has_portal = self._page_has_portal(page)
                        if not has_portal:
                            logger.info(f"b2bindex.jsp 未发现门户元素，判定未登录，页面 {i}, url={current_url}")
                            continue
                        logger.info(f"检测到登录成功（b2bindex.jsp 门户元素已出现），页面 {i}, url={current_url}")
                        self.page = page
                        return True
                    
                    if is_login_page and visible_login_markers:
                        logger.info(f"页面 {i} 仍在登录页，visible_login_markers={visible_login_markers[:3]}")
                    else:
                        logger.info(
                            f"页面 {i} 未判定为已登录: is_login_page={is_login_page}, "
                            f"visible_login_markers={visible_login_markers[:3]}"
                        )
                except Exception as e:
                    logger.debug(f"检查登录状态页面 {i} 时出错: {e}")

        except Exception as e:
            logger.debug(f"登录检查出错: {e}")
            pass
        return False


