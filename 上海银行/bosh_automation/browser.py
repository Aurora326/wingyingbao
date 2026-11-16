# -*- coding: utf-8 -*-
"""
浏览器管理模块 - 上海银行适配版（骨架）

上海银行企业网银通常也要在 TLS 阶段选客户端证书（U 盾），所以登录页导航不能等 goto 返回，
只发起跳转，证书/PIN 弹窗交给 _handle_auth_popups 处理。
"""
from logger import setup_logger
from login_manager import LoginManagerMixin
from common.base_browser import BaseBrowser

logger = setup_logger()


class BOSCBrowser(BaseBrowser, LoginManagerMixin):
    """上海银行浏览器管理器"""

    def navigate_to_login(self):
        """导航到登录页。"""
        if not self.config.login_url:
            raise RuntimeError("config.ini 里没有配置上海银行登录地址（[browser] login_url）")

        if self._check_login_success():
            logger.info("当前已登录，跳过登录页导航")
            return

        logger.info(f"正在访问: {self.config.login_url}")
        try:
            # 证书弹窗出现时 page.goto 会被原生窗口卡住，所以只发起跳转
            self.page.evaluate("(u) => { window.location.href = u; }", self.config.login_url)
            logger.info("登录页导航已发起")
        except Exception as e:
            logger.info(f"登录页导航已发起（{type(e).__name__}）")

        self._handle_auth_popups(timeout=60)

        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception as e:
            logger.warning(f"页面加载等待结束: {e}")
