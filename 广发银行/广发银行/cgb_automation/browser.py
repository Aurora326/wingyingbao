# -*- coding: utf-8 -*-
"""
浏览器管理模块
"""
import os
import sys
import time
import base64
from pathlib import Path
from typing import Optional

# 添加 keyboard 模块路径
sys.path.insert(0, r"C:\wangyingbao\keyboard")

from playwright.sync_api import sync_playwright, Page
from logger import setup_logger

logger = setup_logger()


class CGBBrowser:
    """浏览器管理器"""
    
    def __init__(self, browser_config, credentials=None, mb=None):
        self.config = browser_config
        self.credentials = credentials
        self.mb = mb
        self.playwright = None
        self.browser = None
        self.context = None
        self.page: Optional[Page] = None
        self.mb_robot = None
        
        # 初始化 MB 机柜机器人
        if mb and mb.enabled:
            try:
                from mb_robot import MBRobot
                self.mb_robot = MBRobot(mb)
                logger.info("MB 机柜机器人已初始化")
            except Exception as e:
                logger.warning(f"MB 机柜机器人初始化失败: {e}")

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
        """启动浏览器"""
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

        # 窗口大小
        window_width = 1366
        window_height = 768
        
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
                "--disable-popup-blocking",
                "--safebrowsing-disable-download-protection",
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
        
        # 注入脚本隐藏自动化特征
        self.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        
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

    def _check_login_success(self) -> bool:
        """检查登录是否成功"""
        try:
            current_url = self.page.url
            if "login" not in current_url and "initialData" in current_url:
                logger.info("检测到登录成功")
                return True
            if self.page.locator("text=杜小帅").count() > 0:
                logger.info("检测到登录成功（找到用户名）")
                return True
        except:
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
                if "login" not in current_url and "initialData" in current_url:
                    logger.info("检测到登录成功")
                    return True
                    
                # 检查是否有用户名称元素
                if self.page.locator("text=杜小帅").count() > 0:
                    logger.info("检测到登录成功（找到用户名）")
                    return True
                    
            except:
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
                except:
                    continue
                    
            # 如果找不到，尝试截图验证码区域
            logger.warning("无法找到验证码图片，尝试截图")
            return None
            
        except Exception as e:
            logger.error(f"获取验证码图片失败: {e}")
            return None

    def _recognize_captcha(self, recognizer) -> Optional[str]:
        """识别验证码"""
        if not recognizer:
            return None
            
        try:
            img_data = self._get_captcha_image()
            if img_data:
                # 调用识别器
                result = recognizer.recognize(img_data)
                logger.info(f"验证码识别结果: {result}")
                return result
        except Exception as e:
            logger.error(f"验证码识别失败: {e}")
        return None

    def _aikeyboard_input(self, text: str) -> bool:
        """使用 AiKeyboard.exe 输入文本"""
        try:
            import subprocess
            
            # AiKeyboard.exe 路径
            aikeyboard_path = r"C:\wangyingbao\keyboard\AiKeyboard.exe"
            
            if not os.path.exists(aikeyboard_path):
                logger.error(f"AiKeyboard.exe 不存在: {aikeyboard_path}")
                return False
            
            # 调用 AiKeyboard.exe input 命令输入文本（使用 -k 参数）
            result = subprocess.run(
                [aikeyboard_path, "input", "-k", text],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                return True
            else:
                logger.warning(f"AiKeyboard 执行失败: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"AiKeyboard 输入失败: {e}")
            return False
    
    def _aikeyboard_send(self, key: str) -> bool:
        """使用 AiKeyboard.exe 发送按键"""
        try:
            import subprocess
            
            aikeyboard_path = r"C:\wangyingbao\keyboard\AiKeyboard.exe"
            
            if not os.path.exists(aikeyboard_path):
                return False
            
            result = subprocess.run(
                [aikeyboard_path, "send", "-k", key],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            return result.returncode == 0
                
        except Exception as e:
            logger.error(f"AiKeyboard 发送按键失败: {e}")
            return False

    def login(self, recognizer=None) -> bool:
        """
        执行自动登录（广发银行新版登录页）
        
        Args:
            recognizer: 验证码识别器（可选）
            
        Returns:
            登录是否成功
        """
        logger.info("==================================================")
        logger.info("开始登录流程（新版登录页）...")
        logger.info(f"用户名: '{self.credentials.username}'")
        
        if not self.credentials or not self.credentials.username:
            logger.error("未提供用户名")
            return False
        
        try:
            # 等待页面加载
            logger.info("等待页面加载...")
            time.sleep(5)
            
            # 查找密码输入框（只找可见的）
            password_selectors = [
                'input[type="password"]:visible',
                '#passWord:visible',
                'input[name="passWord"]:visible',
                'input[name="password"]:visible',
                'input[placeholder*="密码"]:visible',
            ]
            
            password_elem = None
            for selector in password_selectors:
                try:
                    elem = self.page.locator(selector).first
                    if elem.count() > 0:
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
            logger.info("使用 AiKeyboard 输入登录密码...")
            password_elem.click()
            time.sleep(0.5)
            
            # 输入密码
            self._aikeyboard_input(self.credentials.password)
            logger.info("AiKeyboard 输入完成")
            time.sleep(0.5)
            
            # 点击登录按钮（使用 JavaScript 点击，避免坐标问题）
            logger.info("点击登录按钮...")
            try:
                # 使用 setTimeout 异步点击，避免阻塞
                self.page.evaluate("""() => {
                    setTimeout(() => {
                        const btn = document.querySelector('#loginButton');
                        if (btn) btn.click();
                    }, 100);
                }""")
                logger.info("使用 JavaScript 点击登录按钮")
            except Exception as e:
                logger.warning(f"JavaScript 点击失败: {e}")
            time.sleep(2)
            
            # 等待Key盾弹窗
            logger.info("等待Key盾弹窗显示...")
            time.sleep(4)
            
            # 处理Key盾弹窗
            logger.info("准备处理Key盾弹窗...")
            pin = self.credentials.pin
            logger.info(f"PIN码存在: {bool(pin)}, 长度: {len(pin) if pin else 0}")
            if pin:
                result = self._handle_keyshield_popup(pin)
                logger.info(f"Key盾处理结果: {result}")
                
                # 使用MB机柜点击物理Key盾OK键
                if result and self.mb_robot:
                    logger.info("等待Key盾显示确认信息...")
                    time.sleep(3)
                    logger.info("使用MB机柜点击物理Key盾OK键...")
                    ok_result = self.mb_robot.click_ok()
                    logger.info(f"MB点击OK结果: {ok_result}")
            else:
                logger.warning("未配置PIN码，等待手动处理Key盾...")
                time.sleep(10)
            
            # 等待登录结果
            time.sleep(5)
            
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
    
    def _handle_keyshield_popup(self, pin: str) -> bool:
        """处理Key盾弹窗（使用AiKeyboard输入PIN+点击确认）"""
        logger.info("【Key盾】开始处理...")
        import pyautogui
        pyautogui.FAILSAFE = False
        
        # 等待弹窗显示
        time.sleep(3)
        
        try:
            # 点击密码输入框
            logger.info("【Key盾】点击密码输入框...")
            pyautogui.click(921, 453)
            time.sleep(0.5)
            
            # 切换到英文输入法
            logger.info("【Key盾】切换英文输入法...")
            self._aikeyboard_send("shift")
            time.sleep(0.3)
            
            # 使用AiKeyboard输入PIN
            logger.info(f"【Key盾】输入PIN码 (长度{len(pin)})...")
            self._aikeyboard_input(pin)
            time.sleep(0.5)
            
            # 点击确认按钮
            logger.info("【Key盾】点击确认按钮...")
            pyautogui.click(988, 600)
            
            logger.info("【Key盾】处理完成")
            return True
        except Exception as e:
            logger.error(f"【Key盾】处理失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def auto_enter_pin_by_clicking(self, pin: str) -> bool:
        """
        使用 AiKeyboard 通过点击方式自动输入 PIN
        
        Args:
            pin: PIN 码
            
        Returns:
            是否输入成功
        """
        logger.info(f"尝试使用 AiKeyboard 输入 PIN...")
        
        if not pin:
            logger.warning("PIN 为空")
            return False
        
        try:
            # 尝试导入 keyboard 模块
            try:
                import keyboard
            except ImportError:
                logger.warning("未安装 keyboard 模块，尝试使用 pip 安装...")
                import subprocess
                subprocess.check_call([sys.executable, "-m", "pip", "install", "keyboard"])
                import keyboard
            
            # 查找 PIN 输入框（通常是密码类型的输入框）
            pin_selectors = [
                'input[type="password"]:not([name="password"])',
                'input[name="pin"]',
                'input[name="PIN"]',
                'input[placeholder*="PIN"]',
                'input[placeholder*="密码"]',
            ]
            
            pin_input = None
            for selector in pin_selectors:
                try:
                    element = self.page.locator(selector).first
                    if element.count() > 0:
                        # 获取元素位置
                        box = element.bounding_box()
                        if box:
                            pin_input = box
                            logger.info(f"找到 PIN 输入框: {selector}")
                            break
                except:
                    continue
            
            if not pin_input:
                logger.warning("未找到 PIN 输入框")
                return False
            
            # 点击 PIN 输入框
            center_x = pin_input['x'] + pin_input['width'] / 2
            center_y = pin_input['y'] + pin_input['height'] / 2
            
            # 使用 mouse 模块点击
            try:
                import mouse
            except ImportError:
                logger.warning("未安装 mouse 模块，尝试使用 pip 安装...")
                import subprocess
                subprocess.check_call([sys.executable, "-m", "pip", "install", "mouse"])
                import mouse
            
            mouse.move(center_x, center_y)
            mouse.click()
            logger.info(f"已点击 PIN 输入框位置: ({center_x}, {center_y})")
            
            # 等待焦点
            time.sleep(0.5)
            
            # 输入 PIN
            for char in pin:
                keyboard.write(char)
                time.sleep(0.05)
            
            logger.info("PIN 输入完成")
            return True
            
        except Exception as e:
            logger.error(f"AiKeyboard 输入 PIN 失败: {e}")
            return False

    def auto_enter_pin(self, pin: str) -> bool:
        """
        使用 Playwright 自动输入 PIN
        
        Args:
            pin: PIN 码
            
        Returns:
            是否输入成功
        """
        logger.info(f"尝试使用 Playwright 输入 PIN...")
        
        if not pin:
            logger.warning("PIN 为空")
            return False
        
        try:
            # 查找 PIN 输入框
            pin_selectors = [
                'input[type="password"]:not([name="password"])',
                'input[name="pin"]',
                'input[name="PIN"]',
                'input[placeholder*="PIN"]',
                'input[placeholder*="密码"]',
            ]
            
            pin_filled = False
            for selector in pin_selectors:
                try:
                    if self.page.locator(selector).count() > 0:
                        self.page.locator(selector).fill(pin)
                        logger.info(f"已使用 Playwright 填写 PIN: {selector}")
                        pin_filled = True
                        break
                except:
                    continue
            
            if not pin_filled:
                logger.warning("未找到 PIN 输入框")
                return False
            
            # 查找并点击确认按钮
            confirm_selectors = [
                'button[type="submit"]',
                'button:has-text("确认")',
                'button:has-text("确定")',
                'button:has-text("OK")',
                'input[type="submit"]',
                '.confirm-btn',
                '#confirmBtn',
            ]
            
            for selector in confirm_selectors:
                try:
                    if self.page.locator(selector).count() > 0:
                        self.page.locator(selector).click()
                        logger.info(f"已点击确认按钮: {selector}")
                        break
                except:
                    continue
            
            logger.info("PIN 输入完成")
            return True
            
        except Exception as e:
            logger.error(f"Playwright 输入 PIN 失败: {e}")
            return False

    def close(self):
        """关闭浏览器"""
        logger.info("正在关闭浏览器...")
        if self.context:
            self.context.close()
        if self.playwright:
            self.playwright.stop()
        logger.info("浏览器已关闭")
