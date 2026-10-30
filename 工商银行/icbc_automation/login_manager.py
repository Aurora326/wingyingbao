# -*- coding: utf-8 -*-
"""
登录逻辑模块 - 工商银行适配版
"""
import time
from logger import setup_logger

logger = setup_logger()


class LoginManagerMixin:
    """登录逻辑 Mixin"""

    def login(self) -> bool:
        """
        执行自动登录（工商银行登录页）

        工商银行企业网银登录流程（根据实际截图）：
        1. 点击 "U盾登录" 按钮
        2. 等待 "选择证书" 弹窗出现
        3. 双击证书列表中的第一条
        4. 等待 "校验U盾密码" 弹窗出现
        5. 输入U盾密码并点击确定
        6. 等待登录成功跳转
        """
        logger.info("==================================================")
        logger.info("开始登录流程（工商银行）...")

        try:
            logger.info("等待页面加载...")
            time.sleep(5)

            page_content = self.page.content()
            if "U盾登录" in page_content or "standard_login_con" in page_content:
                logger.info("检测到工商银行U盾登录页面")
            else:
                logger.warning("未检测到标准U盾登录页面，尝试其他登录方式...")

            if not self._click_ukey_login_button():
                logger.error("未找到U盾登录按钮")
                return False

            logger.info("=" * 50)
            logger.info("步骤2: 等待证书选择弹窗...")
            time.sleep(2)
            self._handle_certificate_selection()

            logger.info("=" * 50)
            logger.info("步骤3: 等待U盾密码弹窗...")
            time.sleep(2)

            pin = self.credentials.pin if self.credentials else None
            if pin:
                self._handle_ukey_password_popup(pin)
                logger.info("等待第二次U盾密码弹窗...")
                time.sleep(5)
                self._handle_ukey_password_popup(pin)
            else:
                logger.info("未配置PIN码，等待用户手动输入U盾密码...")
                time.sleep(30)

            logger.info("=" * 50)
            logger.info("步骤4: 检查登录状态...")
            time.sleep(3)

            if self._check_login_success():
                logger.info("✓ 登录成功")
                return True

            logger.info("登录可能还在处理中，继续检测...")
            for i in range(15):
                time.sleep(2)
                if self._check_login_success():
                    logger.info("✓ 登录成功")
                    return True
                logger.info(f"登录检测中... ({i+1}/15)")

            logger.warning("登录检测超时")
            return False

        except Exception as e:
            logger.error(f"登录过程出错: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def _get_element_screen_position(self, selectors: list) -> tuple:
        """获取元素在屏幕上的坐标（返回逻辑像素，配合默认 DPI Unaware 的 pyautogui）"""
        try:
            for selector in selectors:
                elem = self.page.locator(selector).first
                if elem.count() > 0 and elem.is_visible(timeout=2000):
                    box = elem.bounding_box()
                    if box:
                        window_pos = self.page.evaluate("""() => {
                            return {
                                x: window.screenX || window.screenLeft || 0,
                                y: window.screenY || window.screenTop || 0,
                                outerHeight: window.outerHeight || window.screen.height,
                                innerHeight: window.innerHeight || window.screen.height
                            };
                        }""")

                        chrome_offset_y = window_pos['outerHeight'] - window_pos['innerHeight']
                        screen_x = window_pos['x'] + box['x'] + box['width'] / 2
                        screen_y = window_pos['y'] + chrome_offset_y + box['y'] + box['height'] / 2

                        return (int(screen_x), int(screen_y))
        except Exception as e:
            logger.debug(f"获取元素屏幕坐标失败: {e}")
        return None

    def _click_ukey_login_button(self) -> bool:
        """
        点击页面上的 U盾登录 按钮
        直接使用 pyautogui 坐标点击，避免 Playwright/JS 被轮播图 overlay 拦截
        """
        logger.info("点击 U盾登录 按钮...")

        if "index.html" not in self.page.url:
            logger.warning(f"当前不在登录页，URL={self.page.url}，执行后退...")
            self.page.go_back()
            self.page.wait_for_load_state("networkidle", timeout=5000)

        try:
            import pyautogui
            pyautogui.FAILSAFE = False

            precise_selector = '#app > div > div:nth-child(3) > div > div > div.banner_wrap > div > div.banner_login > div > div.tab-content > div > button'

            pos = self._get_element_screen_position([precise_selector])
            if pos:
                logger.info(f"使用精确屏幕坐标点击 U盾登录: {pos}")
                pyautogui.click(pos[0], pos[1])
            else:
                screen_w, screen_h = pyautogui.size()
                est_x = int(screen_w * 0.70)
                est_y = int(screen_h * 0.46)
                logger.info(f"使用估算坐标点击 U盾登录: ({est_x}, {est_y})")
                pyautogui.click(est_x, est_y)

            time.sleep(1)

            for p in self.context.pages:
                try:
                    if p != self.page and "index.html" not in p.url:
                        logger.warning(f"检测到误触 popup: {p.url}，即将关闭")
                        p.close()
                except Exception:
                    pass

            for p in self.context.pages:
                if "index.html" in p.url:
                    self.page = p
                    break

            return True
        except ImportError:
            logger.error("未安装 pyautogui，无法点击 U盾登录 按钮")
            return False
        except Exception as e:
            logger.error(f"点击 U盾登录 按钮失败: {e}")
            return False

    def _check_login_success(self) -> bool:
        """检查登录是否成功 - 工商银行适配"""
        try:
            current_url = self.page.url
            if "frame/web/index.html" not in current_url and ("corporbank" in current_url or "icbc" in current_url):
                logger.info(f"检测到登录成功（URL跳转）: {current_url}")
                return True

            try:
                if self.page.locator('a:has-text("退出"), button:has-text("退出"), .logout, .exit').count() > 0:
                    logger.info("检测到登录成功（找到退出按钮）")
                    return True

                if self.page.locator('.menu-item:has-text("首页"), .nav-home, [data-menu="home"], .home, text=工作台').count() > 0:
                    logger.info("检测到登录成功（找到首页菜单）")
                    return True

                if self.page.locator('.account-info, .user-info, .welcome-area, .user-name').count() > 0:
                    logger.info("检测到登录成功（找到账户信息区）")
                    return True

                if self.page.locator('text=账户管理, text=转账汇款, text=查询服务, .enterprise-banking').count() > 0:
                    logger.info("检测到登录成功（找到企业网银菜单）")
                    return True
            except Exception:
                pass

        except Exception as e:
            logger.debug(f"登录检查出错: {e}")
            pass
        return False

    def wait_for_manual_login(self, timeout: int = 180) -> bool:
        """等待手动登录完成"""
        logger.info("等待手动登录...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                if self._check_login_success():
                    logger.info("检测到登录成功")
                    return True

                if self.credentials and self.credentials.username:
                    try:
                        if self.page.locator(f"text={self.credentials.username}").count() > 0:
                            logger.info(f"检测到登录成功（找到用户名: {self.credentials.username}）")
                            return True
                    except Exception:
                        pass

            except Exception:
                pass

            time.sleep(2)

        logger.warning("登录超时")
        return False
