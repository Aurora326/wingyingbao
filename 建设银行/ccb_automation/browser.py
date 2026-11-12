# -*- coding: utf-8 -*-
"""
浏览器管理模块 - 建设银行适配版
"""
import sys
from pathlib import Path

_keyboard_path = str(Path(__file__).resolve().parent.parent.parent / "keyboard")
sys.path.insert(0, _keyboard_path)

from playwright.sync_api import Page
from logger import setup_logger
from popup_handler import PopupHandlerMixin
from pin_inputter import PinInputterMixin
from login_manager import LoginManagerMixin
from common.base_browser import BaseBrowser

logger = setup_logger()


class CCBBrowser(BaseBrowser, PopupHandlerMixin, PinInputterMixin, LoginManagerMixin):
    """建设银行浏览器管理器"""

    def _get_extra_launch_args(self) -> list:
        """和工行一样，启动时直接指定自动选择证书的站点，尽量不弹证书框。"""
        return super()._get_extra_launch_args() + [
            "--auto-select-certificate-for-urls=https://b2b.ccb.com",
        ]

    def navigate_to_login(self):
        """导航到登录页 - 建设银行适配"""
        # 设置弹窗监听：自动关闭空白弹窗
        self.page.on("popup", self._close_blank_popup)
        
        # 如果已经登录成功，直接跳过导航（重试时不需要再走登录页）
        if self._check_login_success():
            logger.info("当前已登录，跳过登录页导航")
            return
        
        logger.info(f"正在访问: {self.config.login_url}")
        try:
            # 建行在 TLS 阶段就要求客户端证书和 U盾 PIN，page.goto 会被原生弹窗卡住不返回，
            # 所以只发起跳转，弹窗交给 _handle_auth_popups 处理
            self.page.evaluate("(u) => { window.location.href = u; }", self.config.login_url)
            logger.info("登录页导航已发起")
        except Exception as e:
            logger.info(f"登录页导航已发起（{type(e).__name__}）")

        self._handle_auth_popups(timeout=60)

        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception as e:
            logger.warning(f"页面加载等待结束: {e}")

        # 证书登录页出现后还要填登录密码，提交时会再弹一次 U盾 PIN
        if self._find_password_input_context()[0] is not None:
            logger.info("检测到证书登录表单，填写登录密码并提交...")
            if self._submit_login_password_form():
                self._handle_auth_popups(timeout=60)
                self._adopt_portal_page(timeout=20)
                try:
                    self.page.wait_for_load_state("domcontentloaded", timeout=30000)
                except Exception as e:
                    logger.warning(f"登录后页面加载等待结束: {e}")

    def _handle_auth_popups(self, timeout: float = 60.0) -> None:
        """导航期间处理证书选择和 U盾 PIN 弹窗。

        证书/PIN 都是 Windows 原生弹窗，弹出时 Playwright 会被阻塞，
        必须在页面加载过程中用原生窗口检测处理掉。
        """
        import time as _time

        try:
            from common.windows_ui import find_hwnd
        except ImportError:
            return

        cert_titles = ("选择证书", "证书")
        pin_titles = ("验证通用盾密码", "通用盾密码", "输入密码", "U盾密码", "校验U盾密码")
        deadline = _time.time() + timeout
        last = _time.time()
        handled = 0

        while _time.time() < deadline:
            if find_hwnd(pin_titles):
                handled += 1
                logger.info(f"处理第 {handled} 个 U盾密码弹窗...")
                self._handle_pin_dialog()
                if find_hwnd(pin_titles):
                    logger.warning("PIN 弹窗仍未关闭，停止本轮认证，避免反复尝试")
                    break
                last = _time.time()
                continue
            if find_hwnd(cert_titles):
                self._handle_certificate_dialog()
                last = _time.time()
                continue
            if _time.time() - last > 8:
                break
            _time.sleep(0.3)

        logger.info(f"认证弹窗处理结束（PIN {handled} 次）")

    def _close_blank_popup(self, popup):
        """自动关闭弹窗（公告页、空白页等）"""
        try:
            url = popup.url
            logger.info(f"检测到弹窗: {url}")
            import time

            # 登录结果现在都在主页面 iframe 里，无标题空窗口不再等待跳转，短等后直接关闭
            deadline = time.time() + 2
            while time.time() < deadline:
                url = popup.url or ""
                if url and "about:blank" not in url.lower():
                    break
                time.sleep(0.5)

            url_lower = (url or "").lower()

            # 建行业务页面：直接接管为新主页面，不能关闭
            if "b2b.ccb.com" in url_lower and "b2bindex.jsp" not in url_lower:
                logger.info(f"切换登录结果窗口: {url}")
                self.page = popup
                try:
                    popup.bring_to_front()
                except Exception:
                    pass
                return

            # 关闭空白页和银行公告类弹窗
            if (
                url == "" or
                "about:blank" in url_lower or
                "blank" in url_lower or
                "article" in url_lower or
                "notice" in url_lower or
                "announce" in url_lower or
                "/chn/html1/subject/" in url_lower or
                "subject/" in url_lower
            ):
                logger.info(f"关闭弹窗: {url}")
                popup.close()
        except Exception as e:
            logger.debug(f"关闭弹窗时出错: {e}")

        import time
        time.sleep(2)

        # 检查页面是否加载了登录表单
        try:
            # 等待"立即登录"按钮出现
            self.page.locator('input.pbd_btn_login, input[value="立即登录"]').first.wait_for(state="visible", timeout=10000)
            logger.info("登录页面加载完成，检测到'立即登录'按钮")
        except Exception:
            logger.warning("未检测到'立即登录'按钮，页面可能未完全加载")

        # 注：弹窗处理统一在 main.py 登录成功后调用，避免重复执行
        # 不要点击页面空白处，避免误触广告轮播图
        # 仅按ESC关闭可能的弹层
        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass
