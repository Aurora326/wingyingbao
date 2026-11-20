# -*- coding: utf-8 -*-
"""
弹窗处理模块 - 建设银行适配版
"""
import time
from logger import setup_logger

logger = setup_logger()


class PopupHandlerMixin:
    """弹窗处理 Mixin"""

    def _close_ccb_marketing_popup(self, page) -> bool:
        """关闭建行首页营销弹层（确切元素：#befADV1 > img:nth-child(2)）。"""
        try:
            closed = page.evaluate(
                """() => {
                    const el = document.querySelector('#befADV1 > img:nth-child(2)');
                    if (!el) return false;
                    el.click();
                    return true;
                }"""
            )
            if closed:
                logger.info("已关闭建行营销弹层 (#befADV1 > img:nth-child(2))")
                time.sleep(0.5)
                return True
        except Exception as e:
            logger.debug(f"关闭建行营销弹层失败: {e}")
        return False

    def _dismiss_marketing_overlays(self, timeout: float = 0.0, poll: float = 0.5) -> bool:
        """逐页面/iframe 关闭建行营销遮罩；timeout>0 时轮询等待迟到的弹层。"""
        deadline = time.time() + max(0.0, timeout)
        while True:
            for context in [self.page] + list(self.page.frames):
                try:
                    if self._close_ccb_marketing_popup(context):
                        return True
                except Exception as e:
                    logger.debug(f"关闭营销遮罩失败: {e}")
            if time.time() >= deadline:
                return False
            time.sleep(poll)

    def _is_auxiliary_popup_url(self, url: str) -> bool:
        """判断是否为应关闭的辅助弹窗页。"""
        if not url:
            return True

        url_lower = url.lower()
        popup_markers = [
            "about:blank",
            "article",
            "notice",
            "announce",
            "/chn/html1/subject/",
            "subject/",
            "index.html",
        ]
        return any(marker in url_lower for marker in popup_markers)

    def _close_auxiliary_pages(self) -> bool:
        """关闭登录后出现的公告/通知类子页面。"""
        closed_any = False

        try:
            current_page = self.page
            pages = list(self.page.context.pages)

            main_candidates = []
            for page in pages:
                try:
                    url_lower = (page.url or "").lower()
                    if "b2b.ccb.com" in url_lower and not self._is_auxiliary_popup_url(page.url):
                        main_candidates.append(page)
                except Exception:
                    continue

            if self._is_auxiliary_popup_url(getattr(current_page, "url", "")) and main_candidates:
                self.page = main_candidates[0]
                current_page = self.page
                try:
                    current_page.bring_to_front()
                except Exception:
                    pass

            for page in pages:
                try:
                    if page == current_page:
                        continue

                    url = page.url
                    title = ""
                    try:
                        title = page.title()
                    except Exception:
                        pass

                    if self._is_auxiliary_popup_url(url):
                        logger.info(f"【弹窗处理】关闭子窗口: title={title}, url={url}")
                        page.close()
                        closed_any = True
                except Exception as e:
                    logger.debug(f"关闭子窗口时出错: {e}")
        except Exception as e:
            logger.debug(f"遍历子窗口时出错: {e}")

        return closed_any

    def _dismiss_in_page_popups(self, page) -> bool:
        """关闭主页面内的营销弹层、温馨提示等。"""
        popup_closed = False

        # ===== 策略0：直接基于视口比例坐标点击弹窗关闭按钮 =====
        try:
            # 前置检查：页面上是否确实存在弹窗（有"企业手机银行"文本或大面积fixed层）
            has_popup = page.evaluate("""
                () => {
                    const allEls = Array.from(document.querySelectorAll('*'));
                    const hasText = allEls.some(el => el.textContent && el.textContent.includes('企业手机银行'));
                    const hasFixedPopup = allEls.some(el => {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return (style.position === 'fixed' || style.position === 'absolute')
                            && style.display !== 'none'
                            && rect.width > 300 && rect.height > 200;
                    });
                    return hasText || hasFixedPopup;
                }
            """)
            if not has_popup:
                logger.info("【弹窗处理】未检测到弹窗，跳过坐标点击")
                return popup_closed
            
            viewport = page.evaluate("() => ({ w: window.innerWidth, h: window.innerHeight })")
            if viewport and viewport.get('w') and viewport.get('h'):
                # 从截图看，弹窗在视口中间偏左，右上角 × 约在 (0.47, 0.26) 比例处
                base_x = int(viewport['w'] * 0.47)
                base_y = int(viewport['h'] * 0.26)
                # 只点 3 个点，覆盖关闭按钮附近 12x8 区域，避免误点广告
                offsets = [(0, 0), (-6, -4), (6, -4)]
                for dx, dy in offsets:
                    page.mouse.click(base_x + dx, base_y + dy)
                    time.sleep(0.12)
                logger.info(f"【弹窗处理】视口比例坐标点击弹窗: base=({base_x}, {base_y}) viewport={viewport['w']}x{viewport['h']}")
                time.sleep(0.5)
                popup_closed = True
        except Exception as e:
            logger.warning(f"视口比例坐标点击弹窗失败: {e}")

        specific_close_selectors = [
            '#befADV1 > img:nth-child(2)',
            '#befADV1 img:nth-child(2)',
            '#befADV1 img[onclick*="hide"]',
            '#befADV1 img[src*="mob_drain_close"]',
            '#befADV1 > img[onclick*="hide"]',
            '#befADV1 > img[src*="mob_drain_close"]',
            'img[onclick*="mob_drain"][src*="mob_drain_close"]',
            'img[src*="mob_drain_close.png"]',
            '.mob_drain img[style*="cursor: pointer"]',
            '.mob_drain img[onclick*="hide"]',
            '.mob_drain img[src*="mob_drain_close"]',
            'img[onclick*="hide"]',
            '.layui-layer-close',
            '.layui-layer-setwin a',
            '.ui-dialog-close',
            '.modal-header .close',
            '.popup-close',
            '.msg_close',
            '.notice-close',
            '.btn-close',
            '.close-btn',
            '[title="关闭"]',
            '[aria-label="关闭"]',
            'i.close',
            'span.close',
            'a.close',
            'a:has-text("×")',
            'button:has-text("×")',
        ]

        action_selectors = [
            'button:has-text("关闭")',
            'button:has-text("知道了")',
            'button:has-text("确定")',
            'button:has-text("同意")',
            'a:has-text("关闭")',
            'a:has-text("知道了")',
            'a:has-text("确定")',
        ]

        checkbox_selectors = [
            'label:has-text("不再提醒")',
            'label:has-text("不再提示")',
            'text=不再提醒',
            'text=不再提示',
            'input[type="checkbox"]',
        ]

        for selector in checkbox_selectors:
            try:
                item = page.locator(selector).first
                if item.count() > 0 and item.is_visible(timeout=1000):
                    item.click(timeout=2000)
                    logger.info(f"【弹窗处理】已勾选提示项: {selector}")
                    popup_closed = True
                    break
            except Exception:
                continue

        for selector in specific_close_selectors + action_selectors:
            try:
                btn = page.locator(selector).first
                if btn.count() > 0 and btn.is_visible(timeout=1500):
                    btn.click(timeout=3000)
                    logger.info(f"【弹窗处理】已点击页面弹层控件: {selector}")
                    time.sleep(0.5)
                    popup_closed = True
            except Exception:
                continue

        # ===== 通用弹窗暴力扫描 =====
        try:
            scan_result = page.evaluate("""
                () => {
                    let closed = [];
                    const allEls = Array.from(document.querySelectorAll('*'));
                    
                    // 策略A：文本反向定位——先找包含 "企业手机银行" 的元素，向上追溯弹窗容器
                    const titleEls = allEls.filter(el => {
                        const text = (el.textContent || '').trim();
                        return text.includes('企业手机银行') || text.includes('掌上7*24');
                    });
                    for (const titleEl of titleEls) {
                        let container = titleEl;
                        // 向上追溯最多5层父元素，找到面积足够大的容器
                        for (let i = 0; i < 5; i++) {
                            if (!container.parentElement) break;
                            container = container.parentElement;
                            const rect = container.getBoundingClientRect();
                            if (rect.width > 300 && rect.height > 200) {
                                // 在这个容器里找关闭按钮（右上角小元素）
                                const children = Array.from(container.querySelectorAll('*'));
                                for (const child of children) {
                                    const crect = child.getBoundingClientRect();
                                    const inTopRight = crect.right >= rect.right - 70 && crect.top <= rect.top + 70;
                                    const isSmall = crect.width > 0 && crect.height > 0 && crect.width < 60 && crect.height < 60;
                                    if (inTopRight && isSmall) {
                                        child.click();
                                        closed.push('clicked_ccb_mobile_bank:' + (child.className || 'nocls').substring(0,30));
                                        break;
                                    }
                                }
                                // 如果点击失败，直接隐藏整个容器
                                if (!closed.some(s => s.startsWith('clicked_ccb_mobile_bank'))) {
                                    container.style.display = 'none';
                                    closed.push('hidden_ccb_mobile_bank_container');
                                }
                                break;
                            }
                        }
                    }
                    
                    // 策略B：扫描所有 position: fixed/absolute 且面积较大的元素
                    const fixedEls = allEls.filter(el => {
                        const style = window.getComputedStyle(el);
                        return (style.position === 'fixed' || style.position === 'absolute') 
                            && style.display !== 'none' && style.visibility !== 'hidden';
                    });
                    
                    fixedEls.sort((a, b) => {
                        const ra = a.getBoundingClientRect();
                        const rb = b.getBoundingClientRect();
                        return (rb.width * rb.height) - (ra.width * ra.height);
                    });
                    
                    for (const el of fixedEls.slice(0, 15)) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width < 50 || rect.height < 50) continue;
                        
                        const children = Array.from(el.querySelectorAll('*'));
                        let clicked = false;
                        
                        // 找右上角区域的小元素（× 按钮）
                        for (const child of children) {
                            const crect = child.getBoundingClientRect();
                            const inTopRight = crect.right >= rect.right - 60 && crect.top <= rect.top + 60;
                            const isSmall = crect.width > 0 && crect.height > 0 && crect.width < 50 && crect.height < 50;
                            if (inTopRight && isSmall) {
                                child.click();
                                clicked = true;
                                closed.push('clicked_topright:' + (el.className || 'nocls').substring(0,30));
                                break;
                            }
                        }
                        
                        if (!clicked) {
                            const cls = (el.className || '').toLowerCase();
                            const id = (el.id || '').toLowerCase();
                            const keywords = ['popup', 'modal', 'dialog', 'layer', 'mask', 'adv', 'banner', 
                                              'notice', 'tip', 'msg', 'pop', 'drain', 'promo', 'promotion',
                                              'guide', 'intro', 'welcome', 'announce'];
                            const isModalLike = keywords.some(k => cls.includes(k) || id.includes(k));
                            const zIndex = parseInt(window.getComputedStyle(el).zIndex) || 0;
                            const isOverlay = zIndex > 50 || cls.includes('mask') || cls.includes('overlay') || cls.includes('shade');
                            
                            if (isModalLike || isOverlay) {
                                el.style.display = 'none';
                                closed.push('hidden_fixed:' + (el.className || 'nocls').substring(0,30) + '|z' + zIndex);
                            }
                        }
                    }
                    
                    // 策略C：兜底——z-index > 100 的大面积 fixed 元素直接隐藏（排除右下角提示）
                    const highZ = allEls.filter(el => {
                        const style = window.getComputedStyle(el);
                        const z = parseInt(style.zIndex) || 0;
                        return style.position === 'fixed' && style.display !== 'none' && z > 100;
                    }).sort((a, b) => {
                        const za = parseInt(window.getComputedStyle(a).zIndex) || 0;
                        const zb = parseInt(window.getComputedStyle(b).zIndex) || 0;
                        return zb - za;
                    });
                    
                    for (const el of highZ.slice(0, 3)) {
                        const rect = el.getBoundingClientRect();
                        // 排除右下角的提示（靠右下且面积小）
                        const isBottomRight = rect.left > window.innerWidth * 0.6 && rect.top > window.innerHeight * 0.7;
                        if (rect.width > 200 && rect.height > 150 && !isBottomRight) {
                            el.style.display = 'none';
                            closed.push('hidden_highz:' + (el.className || 'nocls').substring(0,30));
                        }
                    }
                    
                    return closed;
                }
            """)
            if scan_result and len(scan_result) > 0:
                logger.info(f"【弹窗处理】通用扫描关闭弹窗: {scan_result}")
                popup_closed = True
                time.sleep(0.5)
            else:
                logger.info("【弹窗处理】通用扫描未匹配到弹窗")
        except Exception as e:
            logger.warning(f"通用弹窗扫描失败: {e}")

        modal_selectors = [
            '.layui-layer',
            '.modal',
            '.dialog',
            '.popup',
            '.el-dialog',
            '.ant-modal',
            '.ui-dialog',
            '.msg-pop',
        ]

        for selector in modal_selectors:
            try:
                dialog = page.locator(selector).first
                if dialog.count() == 0 or not dialog.is_visible(timeout=1000):
                    continue

                logger.info(f"【弹窗处理】检测到页面弹层: {selector}")

                for inner_selector in ['.close', '.layui-layer-close', '[title="关闭"]', 'button:has-text("关闭")', 'a:has-text("关闭")']:
                    try:
                        btn = dialog.locator(inner_selector).first
                        if btn.count() > 0 and btn.is_visible(timeout=1000):
                            btn.click(timeout=2000)
                            logger.info(f"【弹窗处理】已点击弹层内部控件: {inner_selector}")
                            time.sleep(0.5)
                            popup_closed = True
                            break
                    except Exception:
                        continue
            except Exception:
                continue

        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

        return popup_closed

    def _handle_reconciliation_popup(self):
        """处理登录后可能出现的弹窗（如公告、提示、通用盾提示等）"""
        logger.info("=" * 50)
        logger.info("【弹窗处理】开始检查...")
        
        try:
            import pyautogui
            pyautogui.FAILSAFE = False
            
            # 等待一下让弹窗完全显示
            time.sleep(2)
            
            # 获取屏幕尺寸
            screen_width, screen_height = pyautogui.size()
            logger.info(f"【弹窗处理】屏幕尺寸: {screen_width} x {screen_height}")
            
            popup_closed = False

            # 1. 优先关闭建行首页营销弹层
            if self._close_ccb_marketing_popup(self.page):
                popup_closed = True

            # 2. 关闭登录后自动打开的公告/通知子窗口
            if self._close_auxiliary_pages():
                popup_closed = True

            # 3. 关闭当前主页面中的营销弹层和提示弹层
            if self._dismiss_in_page_popups(self.page):
                popup_closed = True

            # 4. 再扫一遍所有存活页面，处理页面内弹层
            try:
                for page in list(self.page.context.pages):
                    try:
                        if self._close_ccb_marketing_popup(page):
                            popup_closed = True
                        if self._dismiss_in_page_popups(page):
                            popup_closed = True
                    except Exception:
                        continue
            except Exception:
                pass

            # 5. 以上都是探测命中才操作；不再盲按 Esc 或按固定坐标点击浏览器界面
            if not popup_closed:
                logger.info("【弹窗处理】未发现需要处理的弹窗，或弹窗已自动关闭")
            
        except Exception as e:
            logger.error(f"【弹窗处理】处理失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
        logger.info("=" * 50)
    
    def _handle_usbkey_confirm_dialog(self):
        """处理 USBKey 确认提示框（点击确定按钮关闭）"""
        logger.info("检查 USBKey 确认提示框...")
        
        try:
            import pyautogui
            pyautogui.FAILSAFE = False
            
            # 等待提示框出现
            time.sleep(2)
            
            # 获取屏幕尺寸
            screen_width, screen_height = pyautogui.size()
            center_x = screen_width // 2
            center_y = screen_height // 2
            
            # 根据截图，确定按钮在弹窗右下角，取消按钮在左下角
            # 确定按钮位置：中央偏右下方
            # 先尝试按 Tab 键切换到确定按钮（从取消按钮向右切）
            logger.info("尝试按 Tab 键切换到确定按钮...")
            pyautogui.press('tab')  # 从左边的取消切换到右边的确定
            time.sleep(0.3)
            
            # 然后按回车键
            logger.info("按回车键确认...")
            pyautogui.press('return')
            time.sleep(1)
            
            logger.info("已点击确定按钮关闭提示框")
            
        except Exception as e:
            logger.warning(f"自动关闭提示框失败: {e}")
            logger.info("请手动点击确定按钮关闭提示框...")
            time.sleep(5)
