# -*- coding: utf-8 -*-
"""
登录逻辑模块 - 宁波银行适配版
"""
import time
import base64
from typing import Optional
from logger import setup_logger
from common.keyboard_helper import input_with_aikeyboard

logger = setup_logger()


class LoginManagerMixin:
    """登录逻辑 Mixin"""

    def login(self) -> bool:
        """
        执行自动登录（宁波银行登录页）
        
        Returns:
            登录是否成功
        """
        logger.info("==================================================")
        logger.info("开始登录流程（宁波银行）...")
        
        if not self.credentials or not self.credentials.username:
            logger.error("未提供用户名")
            return False
        
        logger.info(f"用户名: '{self.credentials.username}'")
        
        try:
            # 等待页面加载
            logger.info("等待页面加载...")
            time.sleep(5)
            
            # 宁波银行：检查用户名是否已显示（PIN验证后自动显示）
            try:
                # 等待一下让页面完全加载
                time.sleep(2)
                
                # 检查页面上是否显示了企业名称或用户名
                page_content = self.page.content()
                if self.credentials.username and self.credentials.username in page_content:
                    logger.info(f"检测到用户名已显示: {self.credentials.username}")
                else:
                    # 尝试查找只读的用户名显示元素
                    username_display = self.page.locator('.from-box .eb-input__inner[disabled], .login-info, .user-info').first
                    if username_display.count() > 0:
                        display_text = username_display.input_value() if hasattr(username_display, 'input_value') else ""
                        logger.info(f"用户名已显示在页面上: {display_text}")
                    else:
                        logger.info("用户名区域已显示（可能是只读）")
            except Exception as e:
                logger.debug(f"检查用户名显示时出错: {e}")
            
            # 查找密码输入框（只找可见的）- 宁波银行适配
            password_selectors = [
                '.eb-input__inner[type="password"]',  # 宁波银行密码输入框
                'input.eb-input__inner',
                '#_main_rootDiv input[type="password"]',
                'input[type="password"]:visible',
                '#password:visible',
                'input[name="passWord"]:visible',
                'input[name="password"]:visible',
                'input[placeholder*="密码"]:visible',
            ]
            
            password_elem = None
            for selector in password_selectors:
                try:
                    elem = self.page.locator(selector).first
                    if elem.count() > 0 and elem.is_visible(timeout=3000):
                        logger.info(f"找到密码输入框: {selector}")
                        password_elem = elem
                        break
                except Exception as e:
                    logger.debug(f"查找密码框失败 {selector}: {e}")
                    continue
            
            if not password_elem:
                logger.warning("未找到密码输入框")
                return False
            
            # 点击密码输入框
            logger.info("点击密码输入框...")
            password_elem.click()
            time.sleep(0.5)
            
            # 方法1：尝试使用 Playwright fill 输入密码
            try:
                logger.info("使用 Playwright 输入密码...")
                password_elem.fill(self.credentials.password)
                logger.info("Playwright 密码输入完成")
            except Exception as e:
                logger.warning(f"Playwright fill 失败: {e}")
                # 方法2：使用 AiKeyboard
                logger.info("尝试使用 AiKeyboard 输入密码...")
                input_with_aikeyboard(self.credentials.password)
                logger.info("AiKeyboard 密码输入完成")
            
            time.sleep(0.5)
            
            # 检查是否需要验证码
            captcha_input = self.page.locator('input[name="captcha"], input[name="verifyCode"], #captcha, #verifyCode').first
            if captcha_input.count() > 0 and captcha_input.is_visible(timeout=3000):
                logger.info("检测到验证码输入框")
                captcha_text = self._recognize_captcha()
                if captcha_text:
                    captcha_input.fill(captcha_text)
                    logger.info(f"已填写验证码: {captcha_text}")
                else:
                    logger.warning("验证码识别失败，等待手动输入...")
                    time.sleep(10)  # 给用户时间手动输入
            
            # 点击登录按钮
            logger.info("点击登录按钮...")
            login_btn_selectors = [
                'button:has-text("立即登录")',  # 宁波银行登录按钮
                '.login-btn',  # 宁波银行登录按钮class
                'button[type="submit"]',
                'button:has-text("登录")',
                '#loginBtn',
                'a:has-text("登录")',
            ]
            
            btn_clicked = False
            for selector in login_btn_selectors:
                try:
                    btn = self.page.locator(selector).first
                    if btn.count() > 0 and btn.is_visible(timeout=3000):
                        btn.click()
                        logger.info(f"已点击登录按钮: {selector}")
                        btn_clicked = True
                        break
                except Exception:
                    continue
            
            if not btn_clicked:
                # 使用 JavaScript 点击
                try:
                    self.page.evaluate("""() => {
                        const btn = document.querySelector('button[type="submit"], .login-btn, #loginBtn');
                        if (btn) btn.click();
                    }""")
                    logger.info("使用 JavaScript 点击登录按钮")
                except Exception as e:
                    logger.warning(f"JavaScript 点击失败: {e}")
            
            time.sleep(3)
            
            # 检查是否登录成功
            if self._check_login_success():
                logger.info("登录成功")
                return True
            else:
                logger.warning("登录可能未完成，请检查")
                return False
                
        except Exception as e:
            logger.error(f"登录过程出错: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def _check_login_success(self) -> bool:
        """检查登录是否成功 - 宁波银行适配"""
        try:
            current_url = self.page.url
            # 方法1: URL 跳转到主页面
            if "proxyLogin" not in current_url and ("microhome" in current_url or "main" in current_url):
                logger.info("检测到登录成功（URL跳转）")
                return True
            
            # 方法2: 检查登录后才有的特定元素
            try:
                # 检查是否有"退出"按钮（登录后才有）
                if self.page.locator('a:has-text("退出"), button:has-text("退出"), .logout, .exit').count() > 0:
                    logger.info("检测到登录成功（找到退出按钮）")
                    return True
                
                # 检查是否有"首页"菜单（登录后才有）
                if self.page.locator('.menu-item:has-text("首页"), .nav-home, [data-menu="home"], .home').count() > 0:
                    logger.info("检测到登录成功（找到首页菜单）")
                    return True
                    
                # 检查是否有账户信息区域（登录后才有）
                if self.page.locator('.account-info, .user-info, .welcome-area, .user-name').count() > 0:
                    logger.info("检测到登录成功（找到账户信息区）")
                    return True
                    
                # 宁波银行特定：检查工作台或企业网银主页面元素
                if self.page.locator('text=工作台, text=企业网银, .workbench, .dashboard').count() > 0:
                    logger.info("检测到登录成功（找到工作台）")
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
                # 检查是否已经登录成功（URL 变化）
                current_url = self.page.url
                if "proxyLogin" not in current_url and "microhome" in current_url:
                    logger.info("检测到登录成功")
                    return True
                    
                # 检查是否有用户名称元素（使用配置中的用户名）
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

    def _get_captcha_image(self) -> Optional[bytes]:
        """获取验证码图片"""
        try:
            # 尝试查找验证码图片元素
            captcha_selectors = [
                'img[src*="captcha"]',
                'img[src*="verify"]',
                '.captcha img',
                '#captcha img',
                'img[alt*="验证码"]',
                'img[alt*="captcha"]',
                '.verify-code img',
                '#verifyCode img',
            ]
            
            for selector in captcha_selectors:
                try:
                    img = self.page.locator(selector).first
                    if img.count() > 0:
                        # 获取图片的 src 属性
                        src = img.get_attribute('src')
                        if src:
                            if src.startswith('data:image'):
                                # Base64 编码的图片
                                base64_data = src.split(',')[1]
                                return base64.b64decode(base64_data)
                            elif src.startswith('http'):
                                # 通过 JavaScript 获取图片数据
                                img_data = self.page.evaluate("""(selector) => {
                                    const img = document.querySelector(selector);
                                    if (!img) return null;
                                    const canvas = document.createElement('canvas');
                                    canvas.width = img.width;
                                    canvas.height = img.height;
                                    const ctx = canvas.getContext('2d');
                                    ctx.drawImage(img, 0, 0);
                                    return canvas.toDataURL('image/png');
                                }""", selector)
                                if img_data:
                                    base64_data = img_data.split(',')[1]
                                    return base64.b64decode(base64_data)
                except Exception:
                    continue
                    
            # 如果找不到，尝试截图验证码区域
            logger.warning("无法找到验证码图片，尝试截图")
            return None
            
        except Exception as e:
            logger.error(f"获取验证码图片失败: {e}")
            return None

    def _recognize_captcha(self) -> Optional[str]:
        """识别验证码（已移除OCR，直接返回None）"""
        return None
