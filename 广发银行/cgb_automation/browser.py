# -*- coding: utf-8 -*-
"""
浏览器模块 - 广发银行适配版
"""
import os
import time
from pathlib import Path
from typing import Optional
from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext
from logger import setup_logger
from config_loader import BrowserConfig, CredentialsConfig, MBConfig
from captcha_recognizer import CaptchaRecognizer
from mb_robot import MBRobot

logger = setup_logger()


class CGBBrowser:
    """广发银行浏览器自动化类"""

    def __init__(self, config: BrowserConfig, credentials: CredentialsConfig, mb_config: MBConfig = None):
        self.config = config
        self.credentials = credentials
        self.mb_config = mb_config or MBConfig()
        self.mb_robot: Optional[MBRobot] = None
        if self.mb_config.enabled:
            self.mb_robot = MBRobot(self.mb_config)
            logger.info("MB机柜控制已启用")
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    def _find_chrome_path(self) -> Optional[str]:
        """查找 Chrome/Edge 路径"""
        paths = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        ]
        if self.config.chrome_path and Path(self.config.chrome_path).exists():
            return self.config.chrome_path
        for p in paths:
            if Path(p).exists():
                return p
        return None

    def start(self) -> Page:
        """启动浏览器并返回页面对象"""
        logger.info("正在启动浏览器...")
        self.playwright = sync_playwright().start()
        chrome_path = self._find_chrome_path()
        if chrome_path:
            logger.info(f"使用 Chrome: {chrome_path}")

        import sys
        if getattr(sys, 'frozen', False):
            base_dir = Path(sys.executable).parent
        else:
            base_dir = Path(__file__).parent
        
        user_data_dir = base_dir / "chrome_user_data"
        user_data_dir.mkdir(exist_ok=True)
        logger.info(f"用户数据目录: {user_data_dir}")

        window_width = 1380
        window_height = 900
        
        launch_options = {
            "headless": self.config.headless,
            "viewport": {"width": window_width, "height": window_height},
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                f"--window-size={window_width},{window_height}",
                "--ignore-certificate-errors",
            ],
        }
        if chrome_path:
            launch_options["executable_path"] = chrome_path

        if getattr(sys, 'frozen', False):
            download_path = Path(sys.executable).parent / "downloads"
        else:
            download_path = Path(__file__).parent / "downloads"
        download_path.mkdir(exist_ok=True)
        launch_options["downloads_path"] = str(download_path.resolve())
        logger.info(f"浏览器下载路径: {download_path}")

        self.context = self.playwright.chromium.launch_persistent_context(
            str(user_data_dir),
            **launch_options
        )

        pages = self.context.pages
        if pages:
            self.page = pages[0]
        else:
            self.page = self.context.new_page()

        try:
            self.page.evaluate("window.moveTo(0, 0); window.resizeTo(screen.width, screen.height);")
        except:
            pass

        self.page.set_default_timeout(self.config.page_timeout * 1000)
        logger.info("浏览器已启动")
        return self.page

    def navigate_to_login(self):
        """导航到登录页"""
        logger.info(f"正在访问: {self.config.login_url}")
        try:
            self.page.goto(self.config.login_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            logger.warning(f"页面加载超时，继续执行: {e}")
        
        time.sleep(3)
        try:
            self.page.mouse.click(200, 200)
            self.page.keyboard.press("Escape")
        except:
            pass

    def wait_for_manual_login(self, timeout: int = 180) -> bool:
        """等待用户手动完成登录"""
        print("\n" + "=" * 60)
        print("手动登录指引")
        print("1. 在页面中输入用户名、密码、验证码")
        print("2. 点击【立即登录】按钮")
        print("3. 如需UKey验证，请插入UKey并输入密码")
        print("4. 程序将自动检测登录状态")
        print("=" * 60)
        
        logger.info("正在等待登录完成...")
        start = time.time()
        while time.time() - start < timeout:
            if self._check_login_success():
                logger.info("✓ 检测到登录成功！")
                return True
            time.sleep(2)
        
        logger.warning("登录检测超时")
        return False

    def login(self, recognizer: CaptchaRecognizer = None) -> bool:
        """执行登录流程 - 新版登录页适配（无验证码）"""
        logger.info("=" * 50)
        logger.info("开始登录流程（新版登录页）...")
        logger.info(f"用户名: {repr(self.credentials.username)}")
        
        try:
            time.sleep(3)  # 页面完全加载
            
            # 输入用户名（新版页面适配）
            if self.credentials.username:
                try:
                    # 新版页面用户名输入框选择器
                    username_selectors = [
                        "input[placeholder*='用户名']",
                        "input[name='userName']",
                        "input[name='username']",
                        ".el-input__inner",
                    ]
                    for selector in username_selectors:
                        try:
                            username_input = self.page.locator(selector).first
                            if username_input.count() > 0 and username_input.is_visible(timeout=1000):
                                username_input.fill(self.credentials.username)
                                logger.info(f"已输入用户名: {self.credentials.username}")
                                break
                        except:
                            continue
                except:
                    pass
            
            # 使用 AiKeyboard 输入密码
            if self.credentials.password:
                logger.info("使用 AiKeyboard 输入登录密码...")
                self._input_password_with_aikeyboard(self.credentials.password)
            
            # 新版登录页无验证码，直接点击登录
            logger.info("点击登录按钮...")
            
            try:
                import pyautogui
                
                # 获取屏幕尺寸
                screen_w, screen_h = pyautogui.size()
                
                # 新版登录按钮位置（右侧中间偏下）
                btn_x = int(screen_w * 0.70)
                btn_y = int(screen_h * 0.56)
                
                logger.info(f"使用pyautogui点击登录按钮位置: ({btn_x}, {btn_y})")
                pyautogui.click(btn_x, btn_y)
                logger.info("点击已发送")
                
            except Exception as e:
                logger.error(f"pyautogui点击失败: {e}")
            
            # 等待Key盾弹窗显示
            logger.info("等待Key盾弹窗显示...")
            time.sleep(4)
            
            # 处理 Key盾弹窗：输入密码→点击确认→等待→MB点击OK
            if self.credentials.pin:
                logger.info("使用传统方式处理Key盾弹窗（输入密码+点击确认）...")
                self._handle_ukey_dialog(self.credentials.pin)
            
            # 等待UKey处理（5秒给UKey响应时间）
            logger.info("【Key盾处理】等待5秒让UKey处理...")
            time.sleep(5)
            
            # 使用MB机柜点击OK键（如果启用）
            if self.mb_robot and self.mb_config.enabled:
                logger.info("【MB控制】准备使用MB机柜点击OK键...")
                click_result = self.mb_robot.click_ok()
                logger.info(f"【MB控制】OK键点击结果: {click_result}")
                if click_result:
                    logger.info("【MB控制】OK键点击成功")
                else:
                    logger.error("【MB控制】OK键点击失败")
            else:
                logger.info("【MB控制】MB机柜未启用，跳过OK键点击")
            
            # 等待登录结果
            for i in range(15):
                time.sleep(2)
                if self._check_login_success():
                    logger.info("✓ 登录成功！")
                    return True
                logger.info(f"等待登录结果... {i+1}/15")
            
            return False
            
        except Exception as e:
            logger.error(f"登录异常: {e}")
            return False

    def _input_password_with_aikeyboard(self, password: str) -> bool:
        """使用 AiKeyboard 输入密码"""
        try:
            import pyautogui
            
            # 点击密码框
            pwd_input = self.page.locator("input[type='password']").first
            if pwd_input.count() > 0:
                pwd_input.click()
                time.sleep(0.5)
            
            # 使用 AiKeyboard
            aikeyboard_path = self._get_aikeyboard_path()
            if aikeyboard_path and aikeyboard_path.exists():
                import subprocess
                result = subprocess.run(
                    [str(aikeyboard_path), "input", "-k", password],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0:
                    logger.info("AiKeyboard 输入完成")
                    return True
            
            # 回退到 pyautogui
            pyautogui.typewrite(password, interval=0.05)
            return True
            
        except Exception as e:
            logger.error(f"输入密码失败: {e}")
            return False

    def _get_aikeyboard_path(self) -> Path:
        """获取 AiKeyboard.exe 路径"""
        import sys
        if getattr(sys, 'frozen', False):
            exe_dir = Path(sys.executable).parent
        else:
            exe_dir = Path(__file__).parent.parent.parent
        
        possible_paths = [
            exe_dir / "keyboard" / "AiKeyboard.exe",
            exe_dir / ".." / "keyboard" / "AiKeyboard.exe",
            Path.cwd() / "keyboard" / "AiKeyboard.exe",
        ]
        
        for path in possible_paths:
            if path.exists():
                return path
        return None

    def _handle_captcha(self, recognizer: CaptchaRecognizer = None) -> str:
        """处理验证码"""
        try:
            captcha_img = self.page.locator("#verifyImg").first
            if captcha_img.count() == 0:
                return ""
            
            import tempfile
            captcha_path = Path(tempfile.gettempdir()) / "cgb_captcha.png"
            captcha_img.screenshot(path=str(captcha_path))
            
            if recognizer:
                result = recognizer.recognize(captcha_path, use_manual_fallback=False)
                if result:
                    return result
            
            return ""
        except:
            return ""

    def _handle_ukey_dialog(self, pin: str):
        """处理 Key盾弹窗 - 使用AiKeyboard输入密码（绕过安全防护）"""
        try:
            import pyautogui
            
            logger.info("=" * 50)
            logger.info("【Key盾弹窗处理】等待弹窗显示...")
            time.sleep(3)  # 给足时间让弹窗显示
            
            # 获取AiKeyboard路径
            aikeyboard_path = self._get_aikeyboard_path()
            
            if aikeyboard_path and aikeyboard_path.exists():
                # 使用AiKeyboard输入密码
                logger.info(f"使用AiKeyboard输入Key盾密码 (长度: {len(pin)})...")
                import subprocess
                result = subprocess.run(
                    [str(aikeyboard_path), "input", "-k", pin],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0:
                    logger.info("AiKeyboard密码输入完成")
                else:
                    logger.warning(f"AiKeyboard输入失败，回退到pyautogui: {result.stderr}")
                    pyautogui.typewrite(pin, interval=0.05)
            else:
                # 回退到pyautogui
                logger.info(f"AiKeyboard未找到，使用pyautogui输入密码 (长度: {len(pin)})...")
                pyautogui.typewrite(pin, interval=0.05)
            
            time.sleep(0.5)
            
            # 点击"确认"按钮（弹窗左下方的按钮）
            # 弹窗大约在屏幕中心，确认按钮在弹窗左下方
            screen_w, screen_h = pyautogui.size()
            confirm_x = int(screen_w * 0.39)  # 弹窗中心偏左
            confirm_y = int(screen_h * 0.62)  # 弹窗偏下
            
            logger.info(f"点击确认按钮位置: ({confirm_x}, {confirm_y})")
            pyautogui.click(confirm_x, confirm_y)
            logger.info("【Key盾弹窗处理】密码输入完成，已点击确认")
            logger.info("=" * 50)
            
        except Exception as e:
            logger.error(f"【Key盾弹窗处理】失败: {e}")

    def _detect_ukey_confirm_dialog(self) -> bool:
        """检测是否出现UKey确认界面（需要按OK键）"""
        try:
            logger.info("【检测UKey确认界面】开始检测...")
            
            # 检测UKey确认界面的特征文字
            confirm_indicators = [
                "请核对信息",
                "在Key盾按",
                "在key盾按",
                "按OK确认",
                "按'OK'确认",
                '按"OK"确认',
                "按OK",
                "超时时间剩余",  # 确认界面有倒计时
            ]
            
            # 获取页面文本内容
            page_text = self.page.content()
            page_text_lower = page_text.lower()
            
            logger.info(f"【检测UKey确认界面】页面内容长度: {len(page_text)}")
            
            # 打印页面内容的前500字符用于调试
            preview = page_text[:500].replace('\n', ' ').replace('\r', '')
            logger.info(f"【检测UKey确认界面】页面内容预览: {preview}...")
            
            for indicator in confirm_indicators:
                if indicator in page_text:
                    logger.info(f"【检测UKey确认界面】✓ 检测到关键字: '{indicator}'")
                    return True
                if indicator.lower() in page_text_lower:
                    logger.info(f"【检测UKey确认界面】✓ 检测到关键字(小写): '{indicator}'")
                    return True
            
            logger.info("【检测UKey确认界面】✗ 未检测到UKey确认界面")
            return False
        except Exception as e:
            logger.error(f"【检测UKey确认界面】检测异常: {e}")
            return False
    
    def _check_login_success(self) -> bool:
        """检查登录是否成功"""
        try:
            # 首先检查是否在登录页面（如果还在登录页，肯定没登录成功）
            current_url = self.page.url
            if "login" in current_url.lower() or current_url.endswith("/corporbank/"):
                return False
            
            # 检查是否有登录框（有登录框说明还在登录页）
            login_indicators = [
                "input[name='username']",
                "input[type='password']",
                "#loginButton",
                "button:has-text('立即登录')",
            ]
            for selector in login_indicators:
                try:
                    if self.page.locator(selector).first.is_visible(timeout=500):
                        return False  # 还有登录框，说明没登录成功
                except:
                    continue
            
            # 检查登录成功标志
            success_selectors = [
                ".user-name", 
                ".welcome", 
                "text=欢迎",
                ".logout",
                "text=退出",
                "text=账户查询",
                "text=交易明细",
            ]
            for selector in success_selectors:
                try:
                    if self.page.locator(selector).first.is_visible(timeout=500):
                        return True
                except:
                    continue
            
            # 如果页面有错误信息，不算登录成功
            error_indicators = ["error", "404", "请求不存在", "登录失败"]
            page_text = self.page.content()[:1000].lower()
            for error in error_indicators:
                if error in page_text:
                    return False
                    
        except:
            pass
        return False

    def close(self):
        """关闭浏览器"""
        logger.info("正在关闭浏览器...")
        if self.context:
            self.context.close()
        if self.playwright:
            self.playwright.stop()
        logger.info("浏览器已关闭")
