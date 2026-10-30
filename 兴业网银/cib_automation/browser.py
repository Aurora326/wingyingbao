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


class CIBBrowser:
    """兴业银行浏览器自动化类"""

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
        project_root = current_file.parent.parent  # cib_automation 项目根目录

        extension_dir = project_root / "crx_extracted"
        if not extension_dir.exists():
            crx_file = project_root / "CryptoKitExtension.CIB.crx"
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
        """??crx??"""
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

        # 创建用户数据目录
        user_data_dir = Path(__file__).parent / "chrome_user_data"
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

        # 获取屏幕分辨率用于最大化
        # 使用固定窗口大小，避免全屏导致某些元素定位问题
        window_width = 1750
        window_height = 950
        
        launch_options = {
            "headless": self.config.headless,
            "viewport": {"width": window_width, "height": window_height},  # 设置视口为固定大小
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                f"--window-size={window_width},{window_height}",
                # 打印相关设置
                "--kiosk-printing",  # 自动打印，不显示对话框
                "--disable-print-preview",  # 禁用打印预览
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
                logger.warning("headless=true 时不支持 CFCA 扩展，请设置 headless: false")
            else:
                launch_options["args"].extend([
                    f"--disable-extensions-except={self.extension_dir}",
                    f"--load-extension={self.extension_dir}",
                ])
                self.extension_loaded = True
                logger.info(f"加载扩展: {self.extension_dir}")

        # 设置下载路径
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
            self.page.evaluate("(s) => { window.moveTo(0, 0); window.resizeTo(s[0], s[1]); }", [window_width, window_height])
            logger.info(f"浏览器窗口已设置为 {screen_width}x{screen_height}")
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
            if "CFCA" in page_content or "CertEnrollment" in page_content:
                logger.info("检测到 CFCA 扩展已安装")
                return True
                logger.warning("未检测到 CFCA 扩展，请先运行 install_extension.py 安装")
            return False
        except Exception as e:
            logger.warning(f"检查扩展状态时出错: {e}")
            return False

    def navigate_to_login(self):
        """导航到登录页"""
        logger.info(f"正在访问: {self.config.login_url}")
        self.page.goto(self.config.login_url, wait_until="networkidle")
        time.sleep(3)

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
        check_interval = 2  # 每 2 秒检查一次
        
        while time.time() - start < timeout:
            # 检查登录状态
            if self._check_login_success():
                logger.info("✓ 检测到登录成功！")
                return True
            
            # 显示倒计时
            elapsed = int(time.time() - start)
            if elapsed % 5 == 0:  # 每 5 秒输出一次日志
                remaining = timeout - elapsed
                logger.info(f"等待登录中... ({elapsed}s / {timeout}s)，剩余 {remaining}s")
            
            time.sleep(check_interval)
        
        # 超时后询问用户
        logger.warning("登录检测超时")
        print(f"\n⚠ 在 {timeout} 秒内未检测到登录成功")
        try:
            response = input("是否继续等待？(y/n): ").strip().lower()
            if response in ('y', 'yes'):
                return self.wait_for_manual_login(timeout=60)  # 再等待 60 秒
        except KeyboardInterrupt:
            pass
        return False

    def auto_enter_pin(self, pin: str, max_wait: int = 30) -> bool:
        """
        自动输入网盾 PIN 码（支持虚拟键盘）
        
        Args:
            pin: PIN 码（6-12 位数字或字母）
            max_wait: 最大等待弹窗时间（秒）
            
        Returns:
            是否成功输入
        """
        logger.info(f"等待网盾密码输入弹窗...")
        
        # 等待弹窗出现（通过检查页面中是否出现特定元素）
        start = time.time()
        dialog_appeared = False
        
        while time.time() - start < max_wait:
            try:
                # 尝试多种方式检测弹窗
                # 方法1: 检测密码输入框
                pin_input = self.page.locator("input[type='password']").first
                if pin_input.is_visible(timeout=500):
                    dialog_appeared = True
                    logger.info("检测到密码输入框，直接输入...")
                    pin_input.fill(pin)
                    time.sleep(0.5)
                    # 点击确认
                    self._click_confirm_button()
                    return True
            except:
                pass
            
            # 方法2: 尝试通过键盘事件输入（可能绕过虚拟键盘）
            try:
                # 先点击一下弹窗区域使其获得焦点
                self.page.mouse.click(600, 400)
                time.sleep(0.3)
                # 直接发送键盘输入
                self.page.keyboard.type(pin, delay=50)
                time.sleep(0.5)
                # 按回车确认
                self.page.keyboard.press("Enter")
                logger.info("通过键盘事件输入 PIN 完成")
                return True
            except Exception as e:
                logger.debug(f"键盘输入尝试失败: {e}")
            
            time.sleep(1)
        
        if not dialog_appeared:
            logger.warning("未检测到密码输入弹窗，请手动输入")
        return False

    def auto_enter_pin_by_clicking(self, pin: str, max_wait: int = 30) -> bool:
        """
        通过点击虚拟键盘输入 PIN（使用 pyautogui 进行屏幕识别）
        
        Args:
            pin: PIN 码
            max_wait: 最大等待时间
            
        Returns:
            是否成功
        """
        try:
            import pyautogui
            import pyscreeze
        except ImportError:
            logger.warning("未安装 pyautogui，无法使用屏幕点击功能")
            return False
        
        logger.info(f"等待网盾密码弹窗出现...")
        logger.info(f"准备输入 PIN (长度: {len(pin)}, 内容: {pin})")
        time.sleep(2)  # 等待弹窗出现
        
        # 获取浏览器窗口位置
        try:
            import pygetwindow as gw
            windows = gw.getWindowsWithTitle('Chrome')
            if windows:
                win = windows[0]
                win.activate()
                time.sleep(0.5)
        except:
            pass
        
        # 截图并查找虚拟键盘区域
        screenshot = pyautogui.screenshot()
        width, height = screenshot.size
        
        # 虚拟键盘通常在屏幕中下区域
        keyboard_region = (width // 4, height // 2, width // 2, height // 2)
        
        logger.info(f"正在识别虚拟键盘... PIN 长度: {len(pin)}")
        
        # 简化方案：尝试通过相对位置点击
        # 根据截图，虚拟键盘是 4 行 x 10 列的布局
        # 先点击输入框获得焦点
        pyautogui.click(width // 2, height // 2 - 50)
        time.sleep(0.3)
        
        # 检查当前 Caps Lock 状态
        import ctypes
        VK_CAPITAL = 0x14
        caps_lock_on = ctypes.windll.user32.GetKeyState(VK_CAPITAL) & 0x0001 != 0
        logger.info(f"当前 Caps Lock 状态: {'开启' if caps_lock_on else '关闭'}")
        
        # 方法1：使用 Caps Lock 切换大写（更可靠）
        has_upper = any(c.isupper() for c in pin)
        if has_upper and not caps_lock_on:
            logger.info("检测到密码包含大写字母且 Caps Lock 关闭，开启 Caps Lock")
            pyautogui.press('capslock')
            time.sleep(0.3)
        elif not has_upper and caps_lock_on:
            logger.info("密码全为小写但 Caps Lock 开启，关闭 Caps Lock")
            pyautogui.press('capslock')
            time.sleep(0.3)
        
        # 输入密码
        logger.info(f"PIN: {'???' if self.credentials.pin else '???'}")
        for i, char in enumerate(pin):
            pyautogui.press(char.lower() if char.isupper() else char)
            logger.debug(f"输入第 {i+1} 个字符: {char}")
            time.sleep(0.1)
        
        # 恢复 Caps Lock 状态（如果之前是关闭的，现在恢复为关闭）
        if has_upper and not caps_lock_on:
            time.sleep(0.3)
            pyautogui.press('capslock')
            logger.info("恢复 Caps Lock 状态")
        
        time.sleep(0.5)
        pyautogui.press('enter')
        
        logger.info("PIN 输入完成")
        return True

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
        自动选择登录用户并输入密码
        
        Args:
            user_name: 要选择的用户名，如未提供则使用配置中的第一个
            password: 登录密码，如未提供则使用配置中的密码
            
        Returns:
            是否成功
        """
        user_name = user_name or self.credentials.username
        password = password or self.credentials.password
        
        logger.info(f"auto_select_login_user 被调用")
        logger.info(f"user_name: {repr(user_name)}, password: {'已设置' if password else '未设置'}")
        
        if not user_name:
            logger.warning("未配置登录用户名，跳过自动选择")
            return False
            
        logger.info(f"尝试自动选择登录用户: {user_name}")
        
        try:
            # 1. 点击登录名下拉框 - 等待页面完全加载
            time.sleep(2)  # 额外等待确保页面元素加载完成
            
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
                    # 增加等待时间到 3 秒
                    if element.is_visible(timeout=3000):
                        login_input = element
                        logger.info(f"找到登录名输入框: {selector}")
                        break
                except:
                    continue
            
            if not login_input:
                logger.warning("未找到登录名输入框")
                return False
            
            # 点击展开下拉框
            logger.info("点击登录名输入框展开下拉选项...")
            login_input.click()
            time.sleep(1.5)
            
            # 2. 选择指定的用户（通过文本匹配）
            logger.info(f"尝试选择用户: {user_name}")
            try:
                # 方法1: 尝试直接点击包含用户名的选项
                user_option = self.page.locator(f"text={user_name}").first
                if user_option.is_visible(timeout=2000):
                    user_option.click()
                    logger.info(f"✓ 已点击选择用户: {user_name}")
                else:
                    raise Exception("未找到用户选项")
            except Exception as e1:
                logger.debug(f"方法1失败: {e1}")
                try:
                    # 方法2: 尝试在下拉列表中查找
                    dropdown_option = self.page.locator(f".el-select-dropdown__item:has-text('{user_name}')").first
                    if dropdown_option.is_visible(timeout=1000):
                        dropdown_option.click()
                        logger.info(f"✓ 已选择用户: {user_name}")
                    else:
                        raise Exception("下拉列表中未找到")
                except Exception as e2:
                    logger.debug(f"方法2失败: {e2}")
                    # 方法3: 直接输入并回车
                    logger.info("尝试直接输入用户名...")
                    login_input.fill(user_name)
                    self.page.keyboard.press("Enter")
                    logger.info(f"✓ 已输入用户名: {user_name}")
            
            time.sleep(0.5)
            
            # 3. 输入登录密码（如果有配置）
            if password:
                pwd_selectors = [
                    "#password",  # 截图中显示的 ID
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
            
            # 4. 勾选"我已阅读并同意"（如果未勾选）
            try:
                checkbox = self.page.locator("input[type='checkbox']").first
                if checkbox.is_visible(timeout=1000):
                    is_checked = checkbox.is_checked()
                    if not is_checked:
                        checkbox.click()
                        logger.info("已勾选同意条款")
            except:
                pass
            
            time.sleep(0.5)
            
            # 5. 点击登录按钮
            self._click_login_button()
            logger.info("已点击登录按钮")
            
            time.sleep(3)
            return True
            
        except Exception as e:
            logger.error(f"自动选择登录用户失败: {e}")
            return False

    def login(self) -> bool:
        """????"""
        logger.info("=" * 50)
        logger.info("????????...")
        logger.info(f"???: {repr(self.credentials.username)}")
        logger.info(f"??: {'???' if self.credentials.password else '???'}")
        logger.info(f"PIN: {'???' if self.credentials.pin else '???'}")
        
        try:
            logger.info("????????...")
            time.sleep(3)
            
            # ???UKey????
            logger.info("[??1] ??UKey????...")
            if self._try_ukey_login():
                return True
            
            # ??????????????
            logger.info("[??2] ??????????...")
            login_result = self.auto_select_login_user()
            logger.info(f"auto_select_login_user ??: {login_result}")
            if login_result:
                time.sleep(3)
                success = self._check_login_success()
                logger.info(f"_check_login_success ??: {success}")
                if success:
                    return True
            
            # ???????????
            logger.info("[??3] ?????????...")
            if self._try_password_login():
                return True
            
            logger.warning("???????????????")
            manual_timeout = 120
            start = time.time()
            while time.time() - start < manual_timeout:
                if self._check_login_success():
                    logger.info("?????????")
                    return True
                time.sleep(2)
            return False
        except Exception as e:
            logger.error(f"????: {e}")
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
                except:
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
                "input[placeholder*='???']",
                "input[type='text']",
            ]
            user_input = None
            for selector in user_selectors:
                try:
                    element = self.page.locator(selector).first
                    if element.is_visible(timeout=2000):
                        user_input = element
                        break
                except:
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
            "button:has-text('??')",
            "button:has-text('??')",
            "input[type='submit']",
            "a:has-text('??')",
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
            except:
                continue

    def _check_login_success(self) -> bool:
        """检查登录是否成功"""
        success_selectors = [
            ".user-name",
            ".welcome",
            "#main-frame",
            ".main-content",
            "text=欢迎页",
            "text=??",
            ".logout",
            "a:has-text('??')",
        ]
        for selector in success_selectors:
            try:
                if self.page.locator(selector).first.is_visible(timeout=5000):
                    logger.info(f"检测到登录成功元素: {selector}")
                    return True
            except:
                continue
        current_url = self.page.url
        if "login" not in current_url.lower():
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

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.config.auto_close:
            self.close()
