# -*- coding: utf-8 -*-
"""
电子回单采集模块
使用"电子回单查询/补打"页面进行采集
"""
import os
import re
import time
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict
from playwright.sync_api import Page
from logger import setup_logger
try:
    import pyautogui
except Exception:
    pyautogui = None

logger = setup_logger()


class ReceiptCollector:
    """电子回单采集器"""
    
    def __init__(self, page: Page, config: Dict, statement_dates: Optional[Dict] = None):
        self.page = page
        self.config = config
        self.download_path = Path(config.get('download_path', './downloads/receipts'))
        self.download_path.mkdir(parents=True, exist_ok=True)
        self.browser_download_path = Path(__file__).parent / "downloads"
        self.debug_dir = Path(__file__).parent / "debug"
        self.debug_dir.mkdir(exist_ok=True)
        logger.info(f"??????: {self.debug_dir}")
        self.receipt_frame = None  # 保存电子回单iframe
        
        if statement_dates:
            self.start_date = statement_dates.get('start_date', '')
            self.end_date = statement_dates.get('end_date', '')
        else:
            self.start_date = config.get('start_date', '')
            self.end_date = config.get('end_date', '')
            if not self.start_date or not self.end_date:
                days_back = config.get('days_back', 30)
                end_date = datetime.now()
                start_date = end_date - timedelta(days=days_back)
                self.start_date = start_date.strftime("%Y-%m-%d")
                self.end_date = end_date.strftime("%Y-%m-%d")
    
    def collect(self) -> List[str]:
        """采集电子回单"""
        downloaded_files = []
        
        logger.info(f"开始采集电子回单，日期范围: {self.start_date} 至 {self.end_date}")
        
        try:
            # 导航到电子回单页面
            if not self._navigate_to_receipt_page():
                logger.error("导航失败")
                return []
            
            # 设置查询条件
            self._set_query_conditions()
            
            # 点击查询
            if not self._click_query():
                logger.warning("点击查询按钮失败，尝试继续...")
            
            # 等待结果加载
            time.sleep(3)
            
            # 检查是否有数据
            if not self._has_data():
                logger.info("当前查询条件下无电子回单数据")
                return []
            
            # 全选回单
            self._select_all_receipts()
            
            # 点击打印/下载
            self._click_print_download()
            
            # 下载
            file_path = self._download_receipts()
            if file_path:
                downloaded_files.append(file_path)
                    
        except Exception as e:
            logger.error(f"采集电子回单过程出错: {e}")
        
        logger.info(f"电子回单采集完成，共下载 {len(downloaded_files)} 个文件")
        return downloaded_files
    
    def _get_react_frame(self):
        """Get iframe context by id='react-app' if available."""
        for f in self.page.frames:
            try:
                fe = f.frame_element()
                if fe:
                    fid = fe.get_attribute("id") or ""
                    if fid == "react-app":
                        return f
            except Exception:
                continue
        return self.page

    def _get_dialog_frame(self):
        """Get dialog iframe context by id='dialogFrame' if available."""
        for f in self.page.frames:
            try:
                fe = f.frame_element()
                if fe:
                    fid = fe.get_attribute("id") or ""
                    if fid == "dialogFrame":
                        return f
            except Exception:
                continue
        return None

    def _iter_contexts(self):
        """Yield page and all frames for robust dialog search."""
        # Prefer mainframe if present
        for f in self.page.frames:
            try:
                if f.name == "mainframe":
                    yield f
                    break
            except Exception:
                pass
        yield self.page
        for f in self.page.frames:
            yield f

    def _click_confirm_dialog(self) -> bool:
        """Try to click confirm button in any context (page or frames)."""
        # Use unicode escapes to avoid encoding issues in source file.
        confirm = "\u786e\u5b9a"
        confirm_alt = "\u786e\u8ba4"
        selectors = [
            # 主页面选择器
            "body#mainframe .ui-dialog-buttonpane .ui-dialog-buttonset button:nth-child(2)",
            "body#mainframe .ui-dialog-buttonpane .ui-dialog-buttonset button:last-child",
            "body#mainframe .ui-dialog-buttonset button:nth-child(2)",
            "body#mainframe .ui-dialog-buttonset button:last-child",
            # 通用选择器
            f".ui-dialog-buttonpane button:has-text('{confirm}')",
            f".ui-dialog-buttonpane button:has-text('{confirm_alt}')",
            f".ui-dialog-buttonset button:has-text('{confirm}')",
            f".ui-dialog-buttonset button:has-text('{confirm_alt}')",
            f"button:has-text('{confirm}')",
            f"button:has-text('{confirm_alt}')",
            f"span.ui-button-text:has-text('{confirm}')",
            f"span.ui-button-text:has-text('{confirm_alt}')",
            ".ui-dialog-buttonset button:last-child",
            ".ui-dialog-buttonpane button:last-child",
            "body#mainframe .ui-dialog-buttonset button:last-child",
        ]
        for ctx in self._iter_contexts():
            try:
                if ctx.locator(".ui-dialog, .ui-dialog-buttonpane, .ui-dialog-confirm, body#mainframe .ui-dialog").count() == 0:
                    continue
                for sel in selectors:
                    loc = ctx.locator(sel)
                    if loc.count() == 0:
                        continue
                    target = loc.first if "nth-child" in sel else loc.last
                    try:
                        target.scroll_into_view_if_needed()
                        if sel.startswith("span.ui-button-text"):
                            target.locator("..").click(force=True)
                        else:
                            target.click(force=True)
                    except Exception:
                        try:
                            ctx.evaluate("(sel) => { const el = document.querySelector(sel); if (el) el.click(); }", sel)
                        except Exception:
                            pass
                    return True
            except Exception:
                continue
        return False


    def _real_mouse_click(self, locator) -> bool:
        """Click using real mouse at element center."""
        try:
            if locator.count() == 0:
                return False
            el = locator.first
            try:
                el.scroll_into_view_if_needed()
            except Exception:
                pass
            box = el.bounding_box()
            if not box:
                return False
            x = box["x"] + box["width"] / 2
            y = box["y"] + box["height"] / 2
            try:
                self.page.bring_to_front()
            except Exception:
                pass
            self.page.mouse.move(x, y)
            self.page.mouse.click(x, y)
            return True
        except Exception:
            return False

    def _os_click(self, locator) -> bool:
        """Click using OS-level mouse (pyautogui) if available."""
        try:
            if pyautogui is None:
                return False
            if locator.count() == 0:
                return False
            el = locator.first
            try:
                el.scroll_into_view_if_needed()
            except Exception:
                pass
            box = el.bounding_box()
            if not box:
                return False
            x = int(box["x"] + box["width"] / 2)
            y = int(box["y"] + box["height"] / 2)
            try:
                self.page.bring_to_front()
            except Exception:
                pass
            pyautogui.click(x, y)
            return True
        except Exception:
            return False
    def _os_click_by_coords(self, x: int, y: int) -> bool:
        """OS-level click by coordinates."""
        if pyautogui is None:
            return False
        try:
            pyautogui.click(x, y)
            return True
        except Exception:
            return False


    def _debug_screenshot(self, label: str) -> None:
        try:
            ts = time.strftime("%Y%m%d_%H%M%S")
            debug_dir = Path(__file__).parent / "debug"
            debug_dir.mkdir(exist_ok=True)
            path = debug_dir / f"receipt_{label}_{ts}.png"
            self.page.screenshot(path=str(path), full_page=True)
            logger.info(f"debug screenshot saved: {path}")
        except Exception:
            pass

    def _os_click_locator_center(self, locator, label: str) -> bool:
        """OS-level click at locator center with debug screenshot."""
        if pyautogui is None:
            return False
        try:
            if locator.count() == 0:
                return False
            el = locator.first
            try:
                el.scroll_into_view_if_needed()
            except Exception:
                pass
            box = el.bounding_box()
            if not box:
                return False
            x = int(box["x"] + box["width"] / 2)
            y = int(box["y"] + box["height"] / 2)
            self._debug_screenshot(label)
            pyautogui.click(x, y)
            logger.info(f"os click {label} at ({x},{y})")
            return True
        except Exception:
            return False

    def _os_click_by_box_offset(self, box, dx: int, dy: int) -> bool:
        """OS-level click at (box.x+dx, box.y+dy)."""
        if pyautogui is None or not box:
            return False
        try:
            x = int(box["x"] + dx)
            y = int(box["y"] + dy)
            pyautogui.click(x, y)
            logger.info(f"os click at ({x},{y})")
            return True
        except Exception:
            return False

    def _screen_click(self, locator, label: str) -> bool:
        """Click at screen coords computed from element bounding box and window metrics."""
        if pyautogui is None:
            return False
        try:
            if locator.count() == 0:
                return False
            el = locator.first
            try:
                el.scroll_into_view_if_needed()
            except Exception:
                pass
            box = el.bounding_box()
            if not box:
                return False
            metrics = self.page.evaluate('() => ({\n  screenX: window.screenX,\n  screenY: window.screenY,\n  outerWidth: window.outerWidth,\n  outerHeight: window.outerHeight,\n  innerWidth: window.innerWidth,\n  innerHeight: window.innerHeight\n})')
            left_offset = max(0, (metrics["outerWidth"] - metrics["innerWidth"]) / 2)
            top_offset = max(0, metrics["outerHeight"] - metrics["innerHeight"])
            x = int(metrics["screenX"] + left_offset + box["x"] + box["width"] / 2)
            y = int(metrics["screenY"] + top_offset + box["y"] + box["height"] / 2)
            pyautogui.click(x, y)
            logger.info(f"screen click {label} at ({x},{y})")
            return True
        except Exception:
            return False

    def _os_click_box_positions(self, box, positions) -> bool:
        """Click a list of (fx, fy) positions within a bounding box (fractions)."""
        if pyautogui is None or not box:
            return False
        try:
            for fx, fy in positions:
                x = int(box["x"] + box["width"] * fx)
                y = int(box["y"] + box["height"] * fy)
                pyautogui.click(x, y)
                logger.info(f"os click at ({x},{y})")
                time.sleep(0.2)
            return True
        except Exception:
            return False

    def _navigate_to_receipt_page(self) -> bool:
        """Navigate to receipt query page."""
        logger.info("navigate to receipt query page...")

        try:
            time.sleep(2)

            # Find the frame that contains the left menu (iframe page)
            target_ctx = self.page
            for f in self.page.frames:
                try:
                    fe = f.frame_element()
                    if fe:
                        fid = fe.get_attribute("id") or ""
                        if fid == "react-app":
                            target_ctx = f
                            break
                except Exception:
                    continue

            # Fallback: detect by text if iframe id not found
            if target_ctx is self.page:
                for f in self.page.frames:
                    try:
                        if f.locator("text=回单").count() > 0:
                            target_ctx = f
                            break
                    except Exception:
                        continue

            # Locate sidebar container in target context
            sidebar = target_ctx.locator(".bottom-menu.bottom-menu-min, .ant-layout-sider").first
            if sidebar.count() == 0:
                sidebar = target_ctx.locator("body").first

            def _all_contexts():
                yield target_ctx
                for f in self.page.frames:
                    yield f

            def _scroll_sidebar():
                try:
                    sidebar.evaluate("el => el.scrollTo(0, el.scrollHeight)")
                except Exception:
                    try:
                        target_ctx.evaluate("() => { document.querySelector('.ant-layout-sider')?.scrollTo(0, 1000); }")
                    except Exception:
                        pass

            def _click_menu_text(text: str) -> bool:
                # Try sidebar first
                candidates = [
                    sidebar.locator(f".sub-menu-title:has-text('{text}')"),
                    sidebar.locator(f".sub-menu-title >> text={text}"),
                    sidebar.locator(f".menu-item:has-text('{text}')"),
                    sidebar.locator(f".menu-item >> text={text}"),
                    sidebar.locator(f"text={text}"),
                ]
                for loc in candidates:
                    try:
                        if loc.count() == 0:
                            continue
                        target = loc.first
                        try:
                            target.scroll_into_view_if_needed()
                        except Exception:
                            pass
                        target.click()
                        return True
                    except Exception:
                        continue

                # Fallback: search in all frames
                for ctx in _all_contexts():
                    for sel in [
                        f".sub-menu-title:has-text('{text}')",
                        f".sub-menu-title >> text={text}",
                        f".menu-item:has-text('{text}')",
                        f".menu-item >> text={text}",
                        f"text={text}",
                    ]:
                        try:
                            loc = ctx.locator(sel)
                            if loc.count() == 0:
                                continue
                            target = loc.first
                            try:
                                target.scroll_into_view_if_needed()
                            except Exception:
                                pass
                            target.click()
                            return True
                        except Exception:
                            continue
                return False

            # Step 1: collapse "账户查询" (账户明细) to make space
            logger.info("try collapse '账户查询' menu...")
            try:
                account_menu = sidebar.locator(".sub-menu-title >> text=账户查询").first
                if account_menu.count() == 0:
                    account_menu = target_ctx.locator(".sub-menu-title >> text=账户查询").first
                if account_menu.count() > 0:
                    account_menu.click()
                    time.sleep(1)
            except Exception:
                pass

            # Step 2: click "回单" menu
            logger.info("try click '回单' menu...")
            receipt_found = False
            for attempt in range(3):
                try:
                    _scroll_sidebar()
                    time.sleep(0.5)
                    if _click_menu_text("\u56de\u5355"):
                        logger.info("clicked '回单' menu")
                        receipt_found = True
                        break
                except Exception as e:
                    logger.debug(f"attempt {attempt+1} failed: {e}")
                    time.sleep(1)

            if not receipt_found:
                logger.warning("'回单' menu not found")
                return False

            time.sleep(1)

            # Step 3: click "电子回单" menu (parent)
            logger.info("try click '电子回单' menu...")
            e_receipt_found = False
            for attempt in range(3):
                try:
                    _scroll_sidebar()
                    time.sleep(0.5)
                    if _click_menu_text("\u7535\u5b50\u56de\u5355"):
                        logger.info("clicked '电子回单' menu")
                        e_receipt_found = True
                        break
                except Exception as e:
                    logger.debug(f"attempt {attempt+1} failed: {e}")
                    time.sleep(1)

            if not e_receipt_found:
                logger.warning("'电子回单' menu not found")
                return False

            time.sleep(1)

            # Step 4: click "电子回单查询/补打"
            logger.info("try click '电子回单查询/补打'...")
            query_found = False
            for attempt in range(3):
                try:
                    _scroll_sidebar()
                    time.sleep(0.5)
                    query_targets = [
                        "\u7535\u5b50\u56de\u5355\u67e5\u8be2/\u8865\u6253",
                        "\u7535\u5b50\u56de\u5355\u67e5\u8be2",
                        "\u7535\u5b50\u56de\u5355\u67e5\u8be2\u8865\u6253",
                    ]
                    for text in query_targets:
                        if _click_menu_text(text):
                            logger.info(f"clicked '{text}'")
                            query_found = True
                            break

                    if query_found:
                        break
                except Exception as e:
                    logger.debug(f"attempt {attempt+1} failed: {e}")
                    time.sleep(1)

            if not query_found:
                logger.warning("'电子回单查询/补打' menu not found")
                return False

            time.sleep(5)
            
            # 找到并保存电子回单iframe
            for frame in self.page.frames:
                try:
                    if 'queryElectReceipt' in frame.url or '130302' in frame.name:
                        self.receipt_frame = frame
                        logger.info(f"找到并保存电子回单iframe: {frame.url}")
                        break
                except:
                    continue
            
            if not self.receipt_frame:
                logger.warning("未找到电子回单iframe，将使用主页面")
                self.receipt_frame = self.page
            
            return True

        except Exception as e:
            logger.error(f"navigate failed: {e}")
            return False

    def _set_query_conditions(self):
        """设置查询日期条件 - 在iframe中设置"""
        logger.info(f"设置查询日期范围: {self.start_date} 至 {self.end_date}")

        try:
            time.sleep(2)
            
            receipt_frame = self.receipt_frame or self.page
            logger.info(f"使用frame: {receipt_frame}")
            
            # 方法1: 在iframe中使用JavaScript设置日期
            logger.info("尝试在iframe中使用JavaScript设置日期...")
            try:
                receipt_frame.evaluate(f"""() => {{
                    // 移除readonly属性
                    var startEl = document.querySelector('input#startDate');
                    var endEl = document.querySelector('input#endDate');
                    if (startEl) startEl.removeAttribute('readonly');
                    if (endEl) endEl.removeAttribute('readonly');
                    
                    // 设置值
                    if (startEl) startEl.value = '{self.start_date}';
                    if (endEl) endEl.value = '{self.end_date}';
                    
                    // 触发事件
                    if (startEl) startEl.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    if (endEl) endEl.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    
                    return {{startFound: !!startEl, endFound: !!endEl}};
                }}""")
                logger.info(f"JavaScript设置完成: {self.start_date} 至 {self.end_date}")
                time.sleep(1)
            except Exception as e:
                logger.warning(f"JavaScript设置日期失败: {e}")

            # 方法2: 使用Playwright在iframe中设置
            logger.info("尝试在iframe中使用Playwright设置日期...")
            try:
                # 在iframe中通过ID查找
                start_input = receipt_frame.locator("input#startDate").first
                if start_input.count() > 0:
                    start_input.fill(self.start_date)
                    logger.info(f"Playwright设置开始日期: {self.start_date}")
                
                end_input = receipt_frame.locator("input#endDate").first
                if end_input.count() > 0:
                    end_input.fill(self.end_date)
                    logger.info(f"Playwright设置结束日期: {self.end_date}")
                    
                time.sleep(0.5)
            except Exception as e:
                logger.warning(f"Playwright设置日期失败: {e}")

        except Exception as e:
            logger.warning(f"设置日期范围失败: {e}")

    def _click_query(self) -> bool:
        """??????"""
        logger.info("??????...")

        try:
            target_ctx = self.receipt_frame or self.page
            # Prefer explicit id
            try:
                btn = target_ctx.locator("#queryOpeBtn").first
                if btn.count() > 0:
                    btn.click()
                    logger.info("???????(#queryOpeBtn)")
                    time.sleep(3)
                    return True
            except Exception:
                pass

            # Fallback: find by text
            buttons = target_ctx.locator("button, input[type='submit'], input[type='button']").all()
            for btn in buttons:
                try:
                    text = btn.text_content() or btn.get_attribute('value')
                    if text and '??' in text:
                        btn.click()
                        logger.info("???????")
                        time.sleep(3)
                        return True
                except:
                    continue

            logger.warning("???????")
            return False

        except Exception as e:
            logger.error(f"????????: {e}")
            return False

    def _has_data(self) -> bool:
        """检查是否有查询结果"""
        try:
            time.sleep(2)
            
            # 检查"暂无数据"
            try:
                if (self.receipt_frame or self.page).locator("text=暂无数据").is_visible(timeout=2000):
                    logger.info("查询结果为空")
                    return False
            except:
                pass
            
            # 检查表格行
            rows = (self.receipt_frame or self.page).locator("table tbody tr, .ant-table-row").all()
            if len(rows) > 0:
                logger.info(f"查询到 {len(rows)} 条记录")
                return True
            
            return True
        except:
            return True
    
    def _select_all_receipts(self):
        """????"""
        logger.info("????...")

        try:
            target_ctx = self.receipt_frame or self.page
            # Prefer header checkbox label
            try:
                label = target_ctx.locator("#jqgh_cb > label").first
                if label.count() > 0:
                    label.click()
                    logger.info("?????(#jqgh_cb > label)")
                    time.sleep(1)
                    return
            except Exception:
                pass

            # Fallback: click checkbox input
            checkboxes = target_ctx.locator("input[type='checkbox']").all()
            if checkboxes:
                checkboxes[0].click()
                logger.info("?????")
                time.sleep(1)
            else:
                logger.warning("??????")

        except Exception as e:
            logger.warning(f"??????: {e}")

    def _click_print_download(self):
        """点击打印/下载按钮触发下载对话框"""
        logger.info("点击打印/下载按钮...")

        try:
            target_ctx = self.receipt_frame or self.page
            # Prefer explicit id
            try:
                btn = target_ctx.locator("#downloadBtn").first
                if btn.count() > 0:
                    btn.click()
                    logger.info("点击打印/下载按钮(#downloadBtn)")
                    time.sleep(3)  # 等待确认对话框出现
                    return
            except Exception:
                pass

            # Fallback: 通过文本查找
            buttons = target_ctx.locator("button, input[type='button']").all()
            for btn in buttons:
                try:
                    text = btn.text_content() or btn.get_attribute('value')
                    if text and ('打印' in text or '下载' in text or 'PDF' in text):
                        btn.click()
                        logger.info(f"点击打印/下载按钮: {text}")
                        time.sleep(3)  # 等待确认对话框出现
                        return
                except:
                    continue

            logger.warning("未找到打印/下载按钮")

        except Exception as e:
            logger.error(f"点击打印/下载按钮失败: {e}")

    def _download_receipts(self) -> Optional[str]:
        """下载回单 - 处理确认对话框在主页面"""
        logger.info("处理下载对话框...")

        try:
            time.sleep(2)
            before_files = self._get_downloaded_files_before()
            download_start = time.time()

            # 在主页面查找并点击"确定"按钮（确认下载对话框）
            # 这个对话框不在iframe中，而是在主页面

            # 步骤1: 点击下载设置对话框中的下载按钮
            downloaded_file = None
            try:
                time.sleep(1)
                dialog_ctx = self._get_dialog_frame()
                if dialog_ctx:
                    btn = dialog_ctx.locator("#downloadPdf")
                    if btn.count() > 0:
                        self._debug_screenshot("download_settings")
                        logger.info("找到PDF下载按钮(#downloadPdf)")
                        # 直接点击下载按钮
                        btn.first.click()
                        time.sleep(2)
                        self._debug_screenshot("confirm_dialog")
                        
                        # 步骤2: 处理确认对话框 - 点击"确定"按钮
                        logger.info("等待并点击确认对话框...")
                        confirm_clicked = False
                        
                        # 方法1: 使用精确的选择器点击"确定"按钮
                        try:
                            # 等待确认对话框出现
                            self.page.wait_for_selector(".ui-dialog-buttonpane.type-confirm", timeout=5000)
                            confirm_btn = self.page.locator(".ui-dialog-buttonpane.type-confirm .ui-dialog-buttonset button:last-child")
                            if confirm_btn.count() > 0:
                                logger.info(f"找到确定按钮: {confirm_btn.first.text_content()}")
                                confirm_btn.first.click()
                                logger.info("点击确认下载按钮成功")
                                confirm_clicked = True
                                time.sleep(3)
                        except Exception as e:
                            logger.debug(f"方法1点击确认按钮失败: {e}")
                        
                        # 方法2: 使用JavaScript直接点击
                        if not confirm_clicked:
                            try:
                                result = self.page.evaluate("""() => {
                                    // 查找确认对话框中的确定按钮
                                    const buttonpane = document.querySelector('.ui-dialog-buttonpane.type-confirm');
                                    if (buttonpane) {
                                        const buttons = buttonpane.querySelectorAll('.ui-dialog-buttonset button');
                                        for (let btn of buttons) {
                                            if (btn.textContent && btn.textContent.includes('确定')) {
                                                btn.click();
                                                return {success: true, text: btn.textContent};
                                            }
                                        }
                                        // 如果没找到"确定"，点击最后一个按钮
                                        if (buttons.length > 0) {
                                            buttons[buttons.length - 1].click();
                                            return {success: true, method: 'last_button'};
                                        }
                                    }
                                    return {success: false};
                                }""")
                                if result and result.get('success'):
                                    logger.info(f"JavaScript点击确认按钮成功: {result}")
                                    confirm_clicked = True
                                    time.sleep(3)
                            except Exception as e:
                                logger.debug(f"JavaScript点击确认按钮失败: {e}")
                        
                        # 方法3: 使用屏幕坐标点击（备用）
                        if not confirm_clicked:
                            try:
                                dialog = self.page.locator(".ui-dialog.dialogFixed").first
                                if dialog.count() > 0:
                                    box = dialog.bounding_box()
                                    if box:
                                        # 点击对话框右下角区域（确定按钮通常在右侧）
                                        x = box["x"] + box["width"] * 0.75
                                        y = box["y"] + box["height"] * 0.85
                                        self.page.mouse.click(int(x), int(y))
                                        logger.info("使用屏幕坐标点击确认按钮")
                                        confirm_clicked = True
                                        time.sleep(3)
                            except Exception as e:
                                logger.debug(f"屏幕坐标点击确认按钮失败: {e}")
                        
                        if not confirm_clicked:
                            logger.warning("前面的方法未能点击确认按钮，尝试备用方法...")
                            # 备用方法: 使用多种点击方式
                            try:
                                confirm_btn = self.page.locator(".ui-dialog-buttonpane.type-confirm .ui-dialog-buttonset button:last-child")
                                if confirm_btn.count() > 0:
                                    if not self._os_click(confirm_btn):
                                        if not self._os_click_locator_center(confirm_btn, "confirm_btn"):
                                            if not self._screen_click(confirm_btn, "confirm_btn"):
                                                self._real_mouse_click(confirm_btn)
                                    logger.info("备用方法点击确认按钮完成")
                                    time.sleep(3)
                            except Exception as e:
                                logger.debug(f"备用方法点击失败: {e}")
                        
                        # 等待下载完成
                        time.sleep(5)
                        
                        # 尝试通过expect_download捕获下载（如果上面的点击触发了下载）
                        if not downloaded_file:
                            try:
                                with self.page.expect_download(timeout=10000) as dl_info:
                                    pass  # 等待下载事件
                                download = dl_info.value
                                filename = download.suggested_filename or f"receipt_{int(time.time())}.pdf"
                                target = self.browser_download_path / filename
                                download.save_as(str(target))
                                downloaded_file = target
                                logger.info(f"通过expect_download捕获下载: {target}")
                            except Exception:
                                pass  # 没有触发下载事件，继续检查文件
                        
                        time.sleep(2)
            except Exception as e:
                logger.debug(f"处理下载对话框异常: {e}")

            if downloaded_file:
                return self._move_to_download_path(downloaded_file)

            new_file = self._wait_and_find_new_file(before_files, timeout=60, start_time=download_start)
            if new_file:
                return self._move_to_download_path(new_file)

            logger.warning("未检测到新下载文件")
            return None

        except Exception as e:
            logger.error(f"下载回单失败: {e}")
            return None

    def _get_downloaded_files_before(self) -> set:
        paths = [self.browser_download_path, Path.home() / "Downloads"]
        files = set()
        for p in paths:
            if not p.exists():
                continue
            files.update(f.name for f in p.iterdir() if f.is_file())
        return files

    def _wait_and_find_new_file(self, before_files: set, timeout: int = 60, start_time: float = None) -> Optional[Path]:
        start_time = start_time or time.time()
        downloads_candidates = [self.browser_download_path, Path.home() / "Downloads", self.download_path]
        while time.time() - start_time < timeout:
            time.sleep(1)
            for base in downloads_candidates:
                if not base.exists():
                    continue
                current_files = set(f.name for f in base.iterdir() if f.is_file())
                new_files = current_files - before_files
                if not new_files:
                    continue
                new_file_path = base / new_files.pop()
                # ??????
                stable_count = 0
                last_size = -1
                while stable_count < 3:
                    time.sleep(0.5)
                    if new_file_path.exists():
                        current_size = new_file_path.stat().st_size
                        if current_size == last_size:
                            stable_count += 1
                        else:
                            stable_count = 0
                            last_size = current_size
                    else:
                        break
                return new_file_path
        # ????????????(???????????)
        newest = None
        newest_mtime = 0
        for base in downloads_candidates:
            if not base.exists():
                continue
            for f in base.iterdir():
                if not f.is_file():
                    continue
                try:
                    mtime = f.stat().st_mtime
                except Exception:
                    continue
                if mtime >= start_time and mtime > newest_mtime:
                    newest = f
                    newest_mtime = mtime
        return newest

    def _move_to_download_path(self, source_file: Path) -> str:
        """将文件移动到配置的下载目录，按指定格式重命名"""
        # 获取当前日期: yyyymmdd
        timestamp = datetime.now().strftime("%Y%m%d")
        
        # 获取文件扩展名
        ext = source_file.suffix or ".pdf"
        
        # 文件名格式: 网银名称_电子回单_yyyymmdd
        file_name = f"兴业银行_电子回单_{timestamp}{ext}"
        file_path = self.download_path / file_name
        
        # 如果文件已存在，直接覆盖
        if file_path.exists():
            logger.info(f"文件已存在，将覆盖: {file_path}")
        
        shutil.move(str(source_file), str(file_path))
        logger.info(f"电子回单已保存: {file_path}")
        return str(file_path)
