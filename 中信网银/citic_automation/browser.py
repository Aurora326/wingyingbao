# -*- coding: utf-8 -*-
"""
浏览器模块
"""
import os
import time
from pathlib import Path
from typing import Optional
from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext
from logger import setup_logger
from config_loader import BrowserConfig, CredentialsConfig

logger = setup_logger()


class CITICBrowser:
    """中信银行浏览器自动化类"""

    def __init__(self, config: BrowserConfig, credentials: CredentialsConfig):
        self.config = config
        self.credentials = credentials
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.extension_dir: Optional[str] = None
        self.extension_loaded: bool = False
        self.extension_installed_in_profile: bool = False

    def _find_chrome_path(self) -> Optional[str]:
        """查找 Chrome/Edge 路径"""
        paths = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        ]
        # 如果配置了自定义路径
        if self.config.chrome_path and Path(self.config.chrome_path).exists():
            return self.config.chrome_path
        for p in paths:
            if Path(p).exists():
                return p
        return None

    def _get_extension_path(self) -> str:
        """获取扩展路径"""
        current_file = Path(__file__).resolve()
        project_root = current_file.parent.parent  # citic_automation 项目根目录

        extension_dir = project_root / "crx_extracted"
        if not extension_dir.exists():
            crx_file = project_root / "CryptoKitExtension.CITIC.crx"
            if crx_file.exists():
                logger.info("正在解压扩展...")
                self._extract_crx(str(crx_file), str(extension_dir))
            else:
                configured = Path(self.config.extension_path).expanduser().resolve()
                if configured.exists() and configured.suffix.lower() == ".crx":
                    logger.info("正在解压配置路径中的扩展...")
                    self._extract_crx(str(configured), str(extension_dir))

        if extension_dir.exists():
            return str(extension_dir.resolve())

        return str(Path(self.config.extension_path).resolve())

    def _extract_crx(self, crx_path: str, dest_dir: str):
        """解压crx文件"""
        import zipfile
        import struct
        import io

        data = Path(crx_path).read_bytes()
        if data[:4] == b"Cr24":
            version = struct.unpack('<I', data[4:8])[0]
            if version == 2:
                header_len = struct.unpack('<I', data[8:12])[0]
                zip_start = 12 + header_len
            else:
                zip_start = 14
            zip_data = data[zip_start:]
        else:
            zip_data = data

        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            zf.extractall(dest_dir)

        logger.info(f"扩展已解压到: {dest_dir}")

    def _get_extension_id(self) -> Optional[str]:
        """获取扩展 ID"""
        try:
            current_file = Path(__file__).resolve()
            project_root = current_file.parent.parent
            meta_file = project_root / "crx_extracted" / "_metadata" / "verified_contents.json"
            if not meta_file.exists():
                return None
            import json
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                payload = data[0].get("signed_content", {}).get("payload")
                if payload:
                    import base64
                    decoded = base64.b64decode(payload + "===").decode("utf-8")
                    payload_json = json.loads(decoded)
                    return payload_json.get("item_id")
            return None
        except Exception:
            return None

    def _is_extension_installed_in_profile(self, user_data_dir: Path) -> bool:
        """检查扩展是否已安装到用户数据目录"""
        ext_id = self._get_extension_id()
        if not ext_id:
            return False
        ext_root = user_data_dir / "Default" / "Extensions" / ext_id
        if not ext_root.exists():
            return False
        for child in ext_root.iterdir():
            if child.is_dir():
                return True
        return False

    def start(self) -> Page:
        """启动浏览器并返回页面对象"""
        logger.info("正在启动浏览器...")

        self.playwright = sync_playwright().start()

        chrome_path = self._find_chrome_path()
        if chrome_path:
            logger.info(f"使用 Chrome: {chrome_path}")

        # 创建用户数据目录（使用固定目录，避免每次新建）
        import sys
        if getattr(sys, 'frozen', False):
            # 打包后的环境，使用 exe 所在目录
            base_dir = Path(sys.executable).parent
        else:
            # 开发环境
            base_dir = Path(__file__).parent
        
        user_data_dir = base_dir / "chrome_user_data"
        user_data_dir.mkdir(exist_ok=True)
        logger.info(f"用户数据目录: {user_data_dir}")

        installed_in_profile = self._is_extension_installed_in_profile(user_data_dir)
        self.extension_installed_in_profile = installed_in_profile
        if installed_in_profile:
            self.extension_dir = None
            logger.info("扩展已安装到用户数据目录，将直接加载")
        else:
            extension_dir = self._get_extension_path()
            if extension_dir and Path(extension_dir).exists() and Path(extension_dir).is_dir():
                self.extension_dir = extension_dir
            else:
                self.extension_dir = None

        # 使用固定窗口大小
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
                "--kiosk-printing",
                "--disable-print-preview",
                # 禁用下载提示，直接下载到默认目录
                "--disable-download-notification",
                "--disable-features=DownloadBubble,DownloadBubbleV2",
                # 允许自动下载，不弹出保存对话框
                "--safebrowsing-disable-download-protection",
                "--safebrowsing-disable-extension-blacklist",
                # 自动授予下载权限
                "--enable-automation",
                "--allow-file-access",
                "--allow-file-access-from-files",
                "--reduce-security-for-testing",
                "--ignore-certificate-errors",
            ],
        }
        if chrome_path:
            launch_options["executable_path"] = chrome_path

        if installed_in_profile:
            launch_options["ignore_default_args"] = [
                "--enable-automation",
                "--disable-extensions",
                "--disable-component-extensions-with-background-pages",
                "--disable-component-update",
            ]

        if self.extension_dir:
            if self.config.headless:
                logger.warning("headless=true 时不支持安全控件扩展，请设置 headless: false")
            else:
                launch_options["args"].extend([
                    f"--disable-extensions-except={self.extension_dir}",
                    f"--load-extension={self.extension_dir}",
                ])
                self.extension_loaded = True
                logger.info(f"加载扩展: {self.extension_dir}")

        # 设置下载路径（支持打包后的环境）
        if getattr(sys, 'frozen', False):
            download_path = Path(sys.executable).parent / "downloads"
        else:
            download_path = Path(__file__).parent / "downloads"
        download_path.mkdir(exist_ok=True)
        launch_options["downloads_path"] = str(download_path.resolve())
        logger.info(f"浏览器下载路径: {download_path}")

        # 启动浏览器
        self.context = self.playwright.chromium.launch_persistent_context(
            str(user_data_dir),
            **launch_options
        )

        pages = self.context.pages
        if pages:
            self.page = pages[0]
        else:
            self.page = self.context.new_page()

        # 尝试最大化窗口
        try:
            self.page.evaluate("window.moveTo(0, 0); window.resizeTo(screen.width, screen.height);")
            logger.info("浏览器窗口已最大化")
        except Exception as e:
            logger.debug(f"窗口最大化设置失败: {e}")

        self.page.set_default_timeout(self.config.page_timeout * 1000)
        logger.info("浏览器已启动")

        self._check_extension_installed()
        return self.page

    def _check_extension_installed(self) -> bool:
        """检查扩展是否已安装"""
        try:
            if self.extension_loaded or self.extension_installed_in_profile:
                logger.info("扩展已加载")
                return True
            self.page.goto("chrome://extensions/", timeout=10000)
            time.sleep(2)
            page_content = self.page.content()
            if "CFCA" in page_content or "CertEnrollment" in page_content or "中信" in page_content:
                logger.info("检测到安全控件扩展已安装")
                return True
            logger.warning("未检测到安全控件扩展")
            return False
        except Exception as e:
            logger.warning(f"检查扩展状态时出错: {e}")
            return False

    def navigate_to_login(self):
        """导航到登录页"""
        logger.info(f"正在访问: {self.config.login_url}")
        try:
            # 尝试加载页面，使用更短的等待条件
            self.page.goto(self.config.login_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            logger.warning(f"页面加载超时，继续执行: {e}")
        
        time.sleep(5)  # 等待页面渲染
        
        # 确保页面获得焦点，防止地址栏处于激活状态
        try:
            # 点击页面空白区域，确保焦点在页面而非地址栏
            self.page.mouse.click(200, 200)
            logger.info("已点击页面空白区域，确保焦点在页面上")
            time.sleep(0.5)
            
            # 按 Esc 键确保退出任何编辑模式
            self.page.keyboard.press("Escape")
            time.sleep(0.3)
        except Exception as e:
            logger.debug(f"焦点初始化失败: {e}")
        
        # 关闭公告弹窗
        self._close_announcement_dialog()
    
    def _close_announcement_dialog(self):
        """关闭重要信息/公告弹窗"""
        try:
            # 先检查弹窗是否存在
            try:
                advert_pop = self.page.locator("#advertPop").first
                if advert_pop.count() == 0 or not advert_pop.is_visible(timeout=2000):
                    logger.debug("未检测到公告弹窗")
                    return
            except:
                return
            
            logger.info("检测到公告弹窗，尝试关闭...")
            
            # 方式1: 点击关闭按钮
            close_selectors = [
                "#advertPop button.close",
                "#advertPop .modal-header button",
                "#advertPop .close",
                "button.closeAdvertPop",
                ".modal.in button.close",
            ]
            
            for selector in close_selectors:
                try:
                    close_btn = self.page.locator(selector).first
                    if close_btn.count() > 0 and close_btn.is_visible(timeout=1000):
                        close_btn.click(force=True)
                        logger.info(f"已点击关闭按钮: {selector}")
                        time.sleep(1)
                        return
                except:
                    continue
            
            # 方式2: 使用 JavaScript 关闭
            try:
                result = self.page.evaluate("""() => {
                    // 尝试点击关闭按钮
                    const btn = document.querySelector('#advertPop button.close, #advertPop .close');
                    if (btn) {
                        btn.click();
                        return {success: true, method: 'js_click_close'};
                    }
                    // 尝试隐藏弹窗
                    const pop = document.getElementById('advertPop');
                    if (pop) {
                        pop.style.display = 'none';
                        return {success: true, method: 'js_hide'};
                    }
                    return {success: false};
                }""")
                if result and result.get('success'):
                    logger.info(f"使用 JavaScript 关闭公告弹窗: {result.get('method')}")
                    time.sleep(1)
                    return
            except Exception as e:
                logger.debug(f"JavaScript 关闭弹窗失败: {e}")
            
            # 方式3: 按ESC键
            try:
                self.page.keyboard.press("Escape")
                logger.info("按ESC键关闭公告弹窗")
                time.sleep(1)
            except:
                pass
            
        except Exception as e:
            logger.debug(f"关闭公告弹窗失败: {e}")

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

    def auto_enter_pin(self, pin: str, max_wait: int = 30) -> bool:
        """
        中信银行：直接在登录页面输入密码
        
        Args:
            pin: PIN 码（6-12 位数字或字母）
            max_wait: 最大等待时间（秒）
            
        Returns:
            是否成功输入
        """
        logger.info(f"中信银行：在登录页面输入密码...")
        time.sleep(2)
        
        try:
            # 查找登录密码输入框
            password_selectors = [
                "input[placeholder*='登录密码']",
                "input[name='password']",
                "input[type='password']",
                ".login-form input[type='password']",
            ]
            
            for selector in password_selectors:
                try:
                    pwd_input = self.page.locator(selector).first
                    if pwd_input.count() > 0 and pwd_input.is_visible(timeout=3000):
                        pwd_input.fill(pin)
                        logger.info(f"已输入登录密码: {selector}")
                        time.sleep(0.5)
                        
                        # 点击登录按钮
                        self._click_login_button()
                        logger.info("已点击登录按钮")
                        return True
                except:
                    continue
            
            logger.warning("未找到登录密码输入框")
            return False
            
        except Exception as e:
            logger.error(f"输入密码失败: {e}")
            return False

    def _get_aikeyboard_path(self) -> Path:
        """获取 AiKeyboard.exe 的路径（支持多种运行环境）"""
        import sys
        from pathlib import Path
        
        # 获取可执行文件所在目录（支持打包和非打包环境）
        if getattr(sys, 'frozen', False):
            # PyInstaller 打包后的环境
            exe_dir = Path(sys.executable).parent
            logger.debug(f"打包环境，exe_dir: {exe_dir}")
        else:
            # Python 开发环境
            exe_dir = Path(__file__).parent.parent.parent
            logger.debug(f"开发环境，exe_dir: {exe_dir}")
        
        # 尝试多个可能的路径（按优先级排序）
        possible_paths = [
            exe_dir / "keyboard" / "AiKeyboard.exe",           # 发布版：exe 同级目录下的 keyboard
            exe_dir / ".." / "keyboard" / "AiKeyboard.exe",   # 开发环境：项目根目录下的 keyboard
            exe_dir / ".." / ".." / "keyboard" / "AiKeyboard.exe",
            Path.cwd() / "keyboard" / "AiKeyboard.exe",       # 当前工作目录下的 keyboard
        ]
        
        for path in possible_paths:
            path = path.resolve()
            if path.exists():
                logger.info(f"找到 AiKeyboard.exe: {path}")
                return path
        
        logger.warning(f"AiKeyboard.exe 未找到，尝试过的路径: {[str(p.resolve()) for p in possible_paths]}")
        return None
    
    def _input_with_aikeyboard(self, text: str) -> bool:
        """
        使用 AiKeyboard.exe 虚拟键盘输入密码
        命令格式: AiKeyboard.exe input -k <password>
        
        Args:
            text: 要输入的文本
            
        Returns:
            是否成功
        """
        try:
            import subprocess
            
            # 获取 AiKeyboard.exe 路径
            aikeyboard_path = self._get_aikeyboard_path()
            if not aikeyboard_path:
                return False
            
            logger.info(f"使用 AiKeyboard 输入密码...")
            
            # 检查输入法状态
            try:
                import pyautogui
                import ctypes
                
                current_lang = ctypes.windll.user32.GetKeyboardLayout(0)
                lang_id = current_lang & 0xFFFF
                logger.info(f"当前输入法语言 ID: 0x{lang_id:04X}")
                
                # 如果不是英文输入法，切换到英文
                if lang_id != 0x0409 and lang_id != 0x0809:
                    logger.info("检测到非英文输入法，按 Shift 切换...")
                    pyautogui.press('shift')
                    time.sleep(0.5)
                else:
                    logger.info("当前已是英文输入法")
                    
            except Exception as e:
                logger.debug(f"输入法检查/切换失败: {e}")
            
            # 延时2秒确保准备就绪
            time.sleep(2)
            
            # 调用 AiKeyboard.exe input -k <password>
            # AiKeyboard 能正确处理大小写
            result = subprocess.run(
                [str(aikeyboard_path), "input", "-k", text],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                logger.info("AiKeyboard 输入完成")
                return True
            else:
                logger.error(f"AiKeyboard 失败: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"AiKeyboard 调用失败: {e}")
            return False

    def auto_enter_pin_by_clicking(self, pin: str, max_wait: int = 30) -> bool:
        """
        中信银行：使用 AiKeyboard 输入密码
        
        Args:
            pin: PIN code
            max_wait: Max wait time
            
        Returns:
            Success or not
        """
        logger.info(f"中信银行模式：使用 AiKeyboard 输入登录密码...")
        time.sleep(3)  # 增加等待时间
        
        try:
            # 先点击页面空白处，防止浏览器导航栏处于激活状态
            try:
                self.page.mouse.click(200, 200)  # 点击页面左侧空白区域
                logger.info("已点击页面空白区域，确保导航栏不处于激活状态")
                time.sleep(1)
            except Exception as e:
                logger.debug(f"点击空白区域失败: {e}")
            
            # 先等待并查找密码输入框
            password_selectors = [
                "#passwordId",
                "input#passwordId",
                "[id='passwordId']",
                "input[placeholder*='登录密码']",
                "input[type='password']",
                "input[name='password']",
            ]
            
            pwd_input = None
            found_selector = None
            
            # 多次尝试查找
            for attempt in range(3):
                logger.info(f"第 {attempt + 1} 次尝试查找密码输入框...")
                
                for selector in password_selectors:
                    try:
                        element = self.page.locator(selector).first
                        count = element.count()
                        logger.debug(f"选择器 {selector}: count={count}")
                        
                        if count > 0:
                            # 尝试滚动到可见
                            try:
                                element.scroll_into_view_if_needed()
                            except:
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
                # 使用 JavaScript 查找并点击
                result = self.page.evaluate("""() => {
                    const pwd = document.getElementById('passwordId') || 
                               document.querySelector('input[type="password"]');
                    if (pwd) {
                        pwd.focus();
                        pwd.click();
                        return {found: true, id: pwd.id, tag: pwd.tagName};
                    }
                    return {found: false};
                }""")
                logger.info(f"JavaScript 查找结果: {result}")
                
                if result and result.get('found'):
                    time.sleep(0.5)
                    # 使用 AiKeyboard 输入
                    if self._input_with_aikeyboard(pin):
                        time.sleep(0.5)
                        self._click_login_button()
                        return True
                
                return False
            
            # 先确保地址栏不处于激活状态
            try:
                # 方法1: 点击页面空白区域
                self.page.mouse.click(200, 200)
                logger.info("已点击页面空白区域")
                time.sleep(0.5)
                
                # 方法2: 按 Esc 键确保退出地址栏编辑模式
                self.page.keyboard.press("Escape")
                logger.info("已按 Esc 键确保退出地址栏编辑模式")
                time.sleep(0.3)
                
                # 方法3: 再次点击密码框附近区域
                self.page.mouse.click(400, 400)
                logger.info("已点击页面中心区域")
                time.sleep(0.5)
            except Exception as e:
                logger.debug(f"焦点处理失败: {e}")
            
            # 点击输入框获得焦点
            logger.info(f"点击密码输入框 ({found_selector})...")
            pwd_input.click()
            time.sleep(2)  # 延时2秒确保焦点正确
            
            # 使用 AiKeyboard 输入密码
            logger.info("调用 AiKeyboard 输入密码...")
            if self._input_with_aikeyboard(pin):
                logger.info("AiKeyboard 输入完成，等待一下再点击登录...")
                time.sleep(1)
                # 点击登录按钮
                self._click_login_button()
                return True
            
            # 如果 AiKeyboard 失败，回退到 Playwright 方式
            logger.warning("AiKeyboard 失败，使用 Playwright 方式...")
            pwd_input.fill(pin)
            time.sleep(0.5)
            self._click_login_button()
            return True
            
        except Exception as e:
            logger.error(f"输入密码失败: {e}")
            return False

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
            except:
                continue

    def auto_select_login_user(self, user_name: str = None, password: str = None) -> bool:
        """
        自动选择登录用户并输入密码（中信银行通常使用UKey证书登录，此方法可能不适用）
        
        Args:
            user_name: 要选择的用户名
            password: 登录密码
            
        Returns:
            是否成功
        """
        user_name = user_name or self.credentials.username
        password = password or self.credentials.password
        
        logger.info(f"尝试自动登录...")
        logger.info(f"user_name: {repr(user_name)}, password: {'已设置' if password else '未设置'}")
        
        if not user_name:
            logger.warning("未配置登录用户名，跳过自动选择")
            return False
            
        try:
            time.sleep(2)
            
            # 中信银行登录页通常有用户名/密码输入框
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
                except:
                    continue
            
            if not login_input:
                logger.warning("未找到登录名输入框，可能页面使用UKey证书登录")
                return False
            
            # 输入用户名
            login_input.fill(user_name)
            logger.info(f"已输入用户名: {user_name}")
            time.sleep(0.5)
            
            # 输入登录密码
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
                    except:
                        continue
                
                if pwd_input:
                    pwd_input.fill(password)
                    logger.info("已输入登录密码")
                    time.sleep(0.5)
            
            # 点击登录按钮
            self._click_login_button()
            logger.info("已点击登录按钮")
            
            time.sleep(3)
            return True
            
        except Exception as e:
            logger.error(f"自动登录失败: {e}")
            return False

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
            
            # 尝试UKey证书登录（中信银行主要登录方式）
            logger.info("[步骤1] 等待UKey证书登录...")
            if self._try_ukey_login():
                return True
            
            # 尝试用户名密码登录
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
        """
        中信银行 UKey 登录：直接在页面输入密码
        """
        logger.info("中信银行 UKey 登录...")
        try:
            if not self.credentials.pin:
                logger.warning("未配置 PIN，跳过自动输入")
                return False
            
            # 等待页面加载
            time.sleep(2)
            
            # 先点击页面空白处，防止浏览器导航栏处于激活状态
            try:
                self.page.mouse.click(200, 200)  # 点击页面左侧空白区域
                logger.info("已点击页面空白区域，确保导航栏不处于激活状态")
                time.sleep(1)
            except Exception as e:
                logger.debug(f"点击空白区域失败: {e}")
            
            # 先切换到 U盾登录 标签页
            try:
                ukey_tab = self.page.locator("text=U盾登录").first
                if ukey_tab.count() > 0 and ukey_tab.is_visible(timeout=2000):
                    # 检查是否已激活
                    tab_classes = ukey_tab.evaluate("el => el.className")
                    if 'active' not in tab_classes:
                        logger.info("切换到 U盾登录 标签...")
                        ukey_tab.click()
                        time.sleep(1)
                    else:
                        logger.info("已在 U盾登录 标签")
            except Exception as e:
                logger.debug(f"切换标签页检查: {e}")
            
            # 查找登录密码输入框
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
                    except:
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
                    if self._input_with_aikeyboard(self.credentials.pin):
                        logger.info("AiKeyboard 输入完成")
                    else:
                        self.page.evaluate(f"""() => {{
                            const pwd = document.getElementById('passwordId');
                            if (pwd) pwd.value = '{self.credentials.pin}';
                        }}""")
                else:
                    return False
            else:
                # 点击输入框获得焦点
                logger.info(f"点击密码输入框 ({found_selector})...")
                pwd_input.click()
                time.sleep(2)  # 延时2秒确保焦点正确
                
                # 使用 AiKeyboard 输入密码
                if self._input_with_aikeyboard(self.credentials.pin):
                    logger.info("使用 AiKeyboard 输入密码完成")
                else:
                    # 回退到 Playwright 方式
                    logger.warning("AiKeyboard 失败，使用 Playwright 方式...")
                    pwd_input.fill(self.credentials.pin)
            time.sleep(0.5)
            
            # 点击登录按钮
            self._click_login_button()
            logger.info("已点击登录按钮，等待登录结果...")
            
            # 等待登录结果
            time.sleep(5)
            if self._check_login_success():
                logger.info("✓ 登录成功！")
                return True
            
            # 如果没有立即检测到登录成功，再等待一段时间
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

    def _click_login_button(self):
        """点击登录按钮"""
        selectors = [
            "#loginFormContent > button",  # 中信银行登录按钮
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
            except:
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
            except:
                continue
        
        # 检查URL变化
        current_url = self.page.url
        if "login" not in current_url.lower() and "cotb" in current_url.lower():
            logger.info(f"检测到 URL 已变化，可能已登录: {current_url}")
            return True
        
        return False

    def close(self):
        """关闭浏览器"""
        logger.info("正在关闭浏览器...")
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        logger.info("浏览器已关闭")
        # 确保所有日志都被写入文件
        from loguru import logger as loguru_logger
        loguru_logger.complete()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.config.auto_close:
            self.close()
