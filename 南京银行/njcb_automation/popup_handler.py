# -*- coding: utf-8 -*-
"""
弹窗处理模块 - 南京银行适配版
"""
import time
from loguru import logger


class PopupHandlerMixin:
    """弹窗处理 Mixin"""

    def _close_cookie_consent(self):
        """关闭 Cookie 同意弹窗"""
        try:
            cookie_selectors = [
                "text=同意并继续",
                "button:has-text('同意并继续')",
                "button:has-text('同意')",
                "text=同意",
                ".cookie-consent button",
                "[class*='cookie'] button",
            ]
            for selector in cookie_selectors:
                try:
                    btn = self.page.locator(selector).first
                    if btn.count() > 0 and btn.is_visible(timeout=2000):
                        btn.click()
                        logger.info(f"已点击 Cookie 同意按钮: {selector}")
                        time.sleep(0.5)
                        return
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"关闭 Cookie 弹窗失败: {e}")

    def _close_announcement_dialog(self):
        """关闭重要信息/公告弹窗"""
        try:
            # 南京银行可能使用不同的弹窗ID/类名，这里使用通用选择器
            # 实际使用时请根据页面DOM调整
            close_selectors = [
                ".modal-close",
                ".dialog-close",
                ".el-message-box__close",
                "button.close",
                ".modal-header button",
                ".ivu-modal-close",
                ".ant-modal-close",
                "[aria-label='Close']",
                ".layui-layer-close",
                ".layui-layer-close1",
                "text=关闭",
                "text=我知道了",
                "text=暂不",
            ]

            for selector in close_selectors:
                try:
                    close_btn = self.page.locator(selector).first
                    if close_btn.count() > 0 and close_btn.is_visible(timeout=1500):
                        close_btn.click(force=True)
                        logger.info(f"已点击关闭按钮: {selector}")
                        time.sleep(0.5)
                        return
                except Exception:
                    continue

            # 兜底：JS 方式关闭常见弹窗
            result = self.page.evaluate("""() => {
                let closed = false;
                const modalSelectors = [
                    '.modal', '.dialog', '.el-message-box', '.ivu-modal',
                    '.ant-modal', '.layui-layer', '[role="dialog"]'
                ];
                for (const sel of modalSelectors) {
                    const el = document.querySelector(sel);
                    if (el && el.offsetParent !== null) {
                        const closeBtn = el.querySelector('.close, [aria-label="Close"], .layui-layer-close');
                        if (closeBtn) { closeBtn.click(); closed = true; }
                        else { el.style.display = 'none'; closed = true; }
                    }
                }
                return {closed: closed};
            }""")
            if result and result.get('closed'):
                logger.info("使用 JavaScript 关闭公告弹窗")

        except Exception as e:
            logger.debug(f"关闭公告弹窗失败: {e}")

    def _dismiss_post_login_popups(self):
        """强力关闭登录后各类弹窗和提示（公告、通知、满意度调查等）"""
        logger.info("检测并关闭登录后弹窗...")
        time.sleep(2)

        for attempt in range(5):
            result = self.page.evaluate("""
                () => {
                    let closed = false;
                    // 1. 关闭模态弹窗
                    const modalSelectors = [
                        '.modal.in', '.modal.show', '.modal-dialog', '.modal-content',
                        '[role="dialog"]', '.layui-layer', '.el-message-box',
                        '.ant-modal-content', '.ivu-modal-wrap', '.sweet-alert'
                    ];
                    for (const sel of modalSelectors) {
                        const modals = document.querySelectorAll(sel);
                        for (const modal of modals) {
                            if (modal.offsetParent === null) continue;
                            const rect = modal.getBoundingClientRect();
                            if (rect.width < 50 || rect.height < 50) continue;
                            const btns = modal.querySelectorAll('button, a, .close, .layui-layer-close, [aria-label="Close"]');
                            for (const btn of btns) {
                                const txt = btn.textContent.trim();
                                if (['关闭', '确定', '取消', '我知道了', '暂不', 'No', 'Close', 'OK'].includes(txt)
                                    || btn.classList.contains('close')
                                    || btn.getAttribute('aria-label') === 'Close'
                                    || btn.classList.contains('layui-layer-close')) {
                                    btn.click();
                                    closed = true;
                                    break;
                                }
                            }
                            if (closed) break;
                        }
                        if (closed) break;
                    }
                    // 2. 关闭通知条/横幅
                    if (!closed) {
                        const bannerSelectors = [
                            '.alert', '.notification', '.toast', '.banner',
                            '.message', '.notice', '[class*="alert-"]',
                            '[class*="notification-"]', '[class*="toast-"]',
                            '[class*="message-"]', '[class*="notice-"]'
                        ];
                        for (const sel of bannerSelectors) {
                            const banners = document.querySelectorAll(sel);
                            for (const banner of banners) {
                                if (banner.offsetParent === null) continue;
                                const rect = banner.getBoundingClientRect();
                                if (rect.width < 50 || rect.height < 10) continue;
                                const closeBtn = banner.querySelector(
                                    'button.close, .close-btn, .dismiss, [class*="close"], [class*="dismiss"], i.close'
                                );
                                if (closeBtn) {
                                    closeBtn.click();
                                    closed = true;
                                    break;
                                }
                            }
                            if (closed) break;
                        }
                    }
                    return {closed: closed};
                }
            """)
            if not result or not result.get('closed'):
                break
            logger.info("已关闭一层弹窗/通知")
            time.sleep(1)

        # 兜底：ESC + 空白点击
        try:
            self.page.keyboard.press("Escape")
            time.sleep(0.3)
            self.page.mouse.click(100, 100)
        except Exception:
            pass

        logger.info("登录后弹窗清理完成")
