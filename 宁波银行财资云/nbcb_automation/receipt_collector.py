# -*- coding: utf-8 -*-
"""
电子回单采集模块 - 宁波银行财资云适配版

回单下载界面与流水下载界面为同一页面（银行账户明细），
查询出结果后点击"回单下载"按钮即可下载全部回单PDF。
"""
import os
import sys
import time
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict
from playwright.sync_api import Page
from logger import setup_logger
from common.download_helper import get_downloaded_files_before, wait_and_find_new_file

logger = setup_logger()


class ReceiptCollector:
    """电子回单采集器 - 宁波银行财资云适配版"""

    def __init__(self, page: Page, config: Dict, statement_dates: Optional[Dict] = None,
                 browser_download_path: Optional[Path] = None):
        self.page = page
        self.config = config
        self.download_path = Path(config.get('download_path', './downloads/receipts'))
        self.download_path.mkdir(parents=True, exist_ok=True)

        # 设置日期范围（优先使用流水采集的日期）
        if statement_dates:
            self.start_date = statement_dates.get('start_date', '')
            self.end_date = statement_dates.get('end_date', '')
        else:
            self.start_date = config.get('start_date', '')
            self.end_date = config.get('end_date', '')

        # 如果没有设置日期，使用默认值
        if not self.start_date or not self.end_date:
            days_back = config.get('days_back', 30)
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days_back)
            self.start_date = start_date.strftime("%Y-%m-%d")
            self.end_date = end_date.strftime("%Y-%m-%d")

        # 设置归档路径（按年月）
        self.current_date = datetime.now()
        if config.get('archive_by_month', True):
            self.archive_path = self.download_path / f"{self.current_date.year}" / f"{self.current_date.month:02d}"
            self.archive_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"回单归档路径: {self.archive_path}")
            self._cleanup_old_archives()
        else:
            self.archive_path = self.download_path

        # 浏览器下载路径
        if browser_download_path:
            self.browser_download_path = Path(browser_download_path)
            logger.info(f"使用传入的浏览器下载路径: {self.browser_download_path}")
        else:
            if getattr(sys, 'frozen', False):
                self.browser_download_path = Path(sys.executable).parent / "downloads"
            else:
                self.browser_download_path = Path(__file__).parent / "downloads"
        self.browser_download_path.mkdir(parents=True, exist_ok=True)

    def _cleanup_old_archives(self):
        """清理超过保留期限的归档目录"""
        retention_months = self.config.get('retention_months', 2)
        if retention_months <= 0:
            return

        cutoff_date = self.current_date.replace(day=1)
        for _ in range(retention_months):
            if cutoff_date.month == 1:
                cutoff_date = cutoff_date.replace(year=cutoff_date.year - 1, month=12)
            else:
                cutoff_date = cutoff_date.replace(month=cutoff_date.month - 1)

        logger.info(f"清理 {retention_months} 个月前的回单归档数据，截止日期: {cutoff_date.strftime('%Y-%m')}")

        deleted_count = 0
        for year_dir in self.download_path.iterdir():
            if not year_dir.is_dir():
                continue
            try:
                year = int(year_dir.name)
                if year < 2000 or year > 2100:
                    continue
            except ValueError:
                continue

            for month_dir in year_dir.iterdir():
                if not month_dir.is_dir():
                    continue
                try:
                    month = int(month_dir.name)
                    if month < 1 or month > 12:
                        continue
                    dir_date = datetime(year, month, 1)
                    if dir_date < cutoff_date:
                        shutil.rmtree(month_dir)
                        deleted_count += 1
                        logger.info(f"已删除旧归档目录: {year}/{month:02d}")
                except (ValueError, OSError) as e:
                    logger.debug(f"处理目录 {month_dir} 时出错: {e}")
                    continue

        for year_dir in self.download_path.iterdir():
            if year_dir.is_dir() and not any(year_dir.iterdir()):
                year_dir.rmdir()
                logger.info(f"已删除空年份目录: {year_dir.name}")

        if deleted_count > 0:
            logger.info(f"共清理 {deleted_count} 个旧归档目录")
        else:
            logger.info("没有需要清理的旧归档数据")

    def collect(self, skip_navigation: bool = False) -> List[str]:
        """采集电子回单
        
        Args:
            skip_navigation: 如果为 True，跳过导航、日期设置和查询步骤，
                            直接在当前页面点击回单下载（适用于流水已查询后的场景）
        """
        downloaded_files = []
        logger.info(f"开始采集电子回单: {self.start_date} 至 {self.end_date}")

        try:
            if skip_navigation:
                logger.info("跳过导航和查询，直接在当前页面下载回单...")
                # 简单检查当前是否仍在银行账户明细页面
                if not self._is_receipt_page():
                    logger.warning("当前不在银行账户明细页面，尝试重新导航...")
                    skip_navigation = False
                else:
                    logger.info("当前已在银行账户明细页面，直接下载回单")
                    # 流水查询结果为空时，回单同样无数据，需检测后再决定是否下载
                    if not self._has_data():
                        logger.info("无电子回单数据")
                        marker_file = self._create_no_data_marker()
                        return [marker_file] if marker_file else []

            if not skip_navigation:
                # 导航到银行账户明细页面（与流水同一页面）
                nav_success = False
                for retry in range(3):
                    if self._navigate_to_receipt_page():
                        nav_success = True
                        break
                    time.sleep(2)

                if not nav_success:
                    logger.error("导航失败")
                    return []

                # 设置查询条件（日期）
                self._set_query_conditions()

                # 点击查询
                if not self._click_query():
                    logger.warning("点击查询按钮失败")
                    return []

                time.sleep(2)

                # 检查是否有数据
                if not self._has_data():
                    logger.info("无电子回单数据")
                    marker_file = self._create_no_data_marker()
                    return [marker_file] if marker_file else []

            # 下载回单
            file_path = self._download_receipts()
            if file_path:
                downloaded_files.append(file_path)

        except Exception as e:
            logger.error(f"采集电子回单出错: {e}")
            import traceback
            logger.error(traceback.format_exc())

        logger.info(f"电子回单采集完成: {len(downloaded_files)} 个文件")
        return downloaded_files

    # ==================== 页面导航（复用流水模块逻辑） ====================

    def _navigate_to_receipt_page(self) -> bool:
        """导航到银行账户明细页面（与流水同一页面）"""
        logger.info("导航到银行账户明细页面（回单与流水同一页面）...")
        time.sleep(2)

        try:
            # 1. 点击左侧"报表中心"图标菜单
            report_menu_selectors = [
                '[title="报表中心"]',
                'text=报表中心',
                ':has-text("报表中心")',
            ]

            report_menu_clicked = False
            for selector in report_menu_selectors:
                try:
                    elem = self.page.locator(selector).first
                    elem.click(timeout=5000)
                    logger.info(f"点击报表中心菜单: {selector}")
                    report_menu_clicked = True
                    time.sleep(2)
                    break
                except Exception:
                    continue

            if not report_menu_clicked:
                logger.warning("未找到报表中心菜单，尝试直接点击左侧图标...")
                try:
                    icons = self.page.locator('aside .el-menu-item, aside [class*="menu-item"], aside svg, aside i').all()
                    if len(icons) >= 4:
                        icons[3].click()
                        time.sleep(2)
                        report_menu_clicked = True
                except Exception:
                    pass

            # 2. 点击"银行账户明细"（精确匹配，排除"银行账户明细维护"）
            detail_menu_clicked = False

            # 方法1: JS TreeWalker 精确匹配
            try:
                js_result = self.page.evaluate("""() => {
                    var spans = document.querySelectorAll(
                        '.sider-menu-list-second li.second-menu-item-level-3 span.ellpsis-text'
                    );
                    for (var i = 0; i < spans.length; i++) {
                        var span = spans[i];
                        var text = (span.innerText || span.textContent || '').trim();
                        if (text === '银行账户明细') {
                            var item = span.closest('li.second-menu-item-level-3');
                            if (!item) continue;
                            var itemText = (item.innerText || item.textContent || '').trim();
                            if (itemText !== '银行账户明细') continue;
                            var target = item.querySelector('.second-menu-item-text') || item;
                            var rect = target.getBoundingClientRect();
                            if (rect.width <= 0 || rect.height <= 0) continue;
                            target.click();
                            return {found: true, method: 'exact_level3_span'};
                        }
                    }
                    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
                    var node;
                    while (node = walker.nextNode()) {
                        if (node.textContent.trim() === '银行账户明细') {
                            var item = node.parentElement ? node.parentElement.closest('li.second-menu-item-level-3') : null;
                            if (!item) continue;
                            var itemText = (item.innerText || item.textContent || '').trim();
                            if (itemText !== '银行账户明细') continue;
                            var target = item.querySelector('.second-menu-item-text') || item;
                            var rect = target.getBoundingClientRect();
                            if (rect.width <= 0 || rect.height <= 0) continue;
                            target.click();
                            return {found: true, method: 'exact_text_node_level3'};
                        }
                    }
                    return {found: false};
                }""")
                if js_result.get('found'):
                    logger.info(f"JS TreeWalker 点击: {js_result}")
                    time.sleep(4)
                    if self._is_receipt_page():
                        detail_menu_clicked = True
            except Exception as e:
                logger.warning(f"JS TreeWalker 失败: {e}")

            # 方法2: Playwright exact 匹配 + 祖先验证
            if not detail_menu_clicked:
                candidates = self.page.get_by_text("银行账户明细", exact=True).all()
                logger.info(f"get_by_text(exact=True) 找到 {len(candidates)} 个候选元素")
                for idx, elem in enumerate(candidates):
                    try:
                        check = elem.evaluate("""el => {
                            var self = (el.innerText || '').trim();
                            var p = el.parentElement;
                            var gp = p ? p.parentElement : null;
                            var pText = p ? (p.innerText || '').trim() : '';
                            var gpText = gp ? (gp.innerText || '').trim() : '';
                            return {
                                self: self, pText: pText, gpText: gpText,
                                hasWeihu: self.includes('维护') || pText.includes('维护') || gpText.includes('维护')
                            };
                        }""")
                        if check.get('hasWeihu'):
                            logger.warning(f"候选{idx+1} 包含'维护'，跳过")
                            continue
                        elem.click(timeout=5000)
                        logger.info(f"已点击候选 {idx+1}")
                        time.sleep(4)
                        if self._is_receipt_page():
                            detail_menu_clicked = True
                            break
                    except Exception:
                        continue

            # 方法3: JS 兜底
            if not detail_menu_clicked:
                js_result = self.page.evaluate("""() => {
                    var all = document.querySelectorAll('body *');
                    for (var i=0; i<all.length; i++) {
                        var el = all[i];
                        var txt = (el.innerText || el.textContent || '').trim();
                        if (txt === '银行账户明细') {
                            var p = el.parentElement;
                            var gp = p ? p.parentElement : null;
                            var pTxt = p ? p.innerText.trim() : '';
                            var gpTxt = gp ? gp.innerText.trim() : '';
                            if (pTxt.includes('维护') || gpTxt.includes('维护')) continue;
                            el.click();
                            return {found: true};
                        }
                    }
                    return {found: false};
                }""")
                if js_result.get('found'):
                    logger.info("JS 兜底点击成功")
                    time.sleep(4)
                    if self._is_receipt_page():
                        detail_menu_clicked = True

            if not detail_menu_clicked:
                logger.warning("未找到银行账户明细菜单")
                return False

            logger.info("✓ 已成功进入银行账户明细页面")
            return True

        except Exception as e:
            logger.error(f"导航失败: {e}")
            return False

    def _is_receipt_page(self) -> bool:
        """检查当前是否在银行账户明细页面"""
        # 先排除错误的"银行账户明细维护"页面。
        # 以顶部主标签栏的【激活页签】为准——未激活的"银行账户明细维护"页签
        # 只是残留 tab（文本同样可见），不能据此误判当前页面。
        try:
            wrong_page = self.page.evaluate("""() => {
                // 主标签栏页签带有 .main-label-home / .main-label-other 自定义结构，
                // 页面内容里的其它 el-tabs（如"已处理/当日"）没有该结构
                var actives = document.querySelectorAll('.el-tabs__item.is-active');
                for (var i = 0; i < actives.length; i++) {
                    var label = actives[i].querySelector('.main-label-other .text, .main-label-home .text');
                    if (!label) continue;
                    var t = (label.innerText || label.textContent || '').trim();
                    if (t === '银行账户明细维护') return true;
                    if (t === '银行账户明细') return false;
                }
                // 无法识别激活页签时退回旧逻辑：排除侧边菜单和标签栏后查找可见的"维护"标题
                var nodes = Array.from(document.querySelectorAll('body *'));
                return nodes.some(function(el) {
                    var text = (el.innerText || el.textContent || '').trim();
                    if (text !== '银行账户明细维护') return false;
                    if (el.closest('.sider-menu-list-second, .sider-menu-ul, aside, .el-tabs__header')) return false;
                    var rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                });
            }""")
            if wrong_page:
                logger.warning("检测到错误页面: 银行账户明细维护")
                return False
        except Exception:
            pass

        contexts = [self.page] + list(self.page.frames)
        indicators = [
            'input[placeholder*="起始时间"]',
            'input[placeholder*="终止时间"]',
            'input[placeholder="请选择交易日期"]',
            'label[for="startTime"]',
            'label[for="endTime"]',
            '.eb-range-input',
            'input[name="startDate"]',
            'input[name="endDate"]',
            'input[name="beginDate"]',
            "button:has-text('查询')",
            "button:has-text('下载')",
        ]

        for context in contexts:
            for indicator in indicators:
                try:
                    locator = context.locator(indicator).first
                    if locator.is_visible(timeout=2000):
                        logger.info(f"检测到明细页面: {indicator}")
                        return True
                except Exception:
                    continue
        return False

    # ==================== 查询条件设置（复用流水模块逻辑） ====================

    def _set_query_conditions(self):
        """设置查询日期条件"""
        logger.info(f"设置日期: {self.start_date} 至 {self.end_date}")

        try:
            time.sleep(1)
            page_or_frame = self._get_report_frame()

            # 设置开始日期和结束日期
            self._set_date_field_by_playwright("startDate", self.start_date, page_or_frame)
            self._set_date_field_by_playwright("beginDate", self.start_date, page_or_frame)
            self._set_date_field_by_playwright("endDate", self.end_date, page_or_frame)

            # 关闭日历
            self.page.keyboard.press("Escape")
            time.sleep(0.2)
            self.page.keyboard.press("Escape")
            time.sleep(0.2)

            # 点击空白处关闭日历
            try:
                page_or_frame.evaluate("() => { document.activeElement && document.activeElement.blur(); }")
                time.sleep(0.2)
            except Exception:
                pass

        except Exception as e:
            logger.warning(f"设置日期范围失败: {e}")

    def _set_date_field_by_playwright(self, field_id: str, date_value: str, page_or_frame=None):
        """使用 Playwright 直接设置日期 - 宁波银行财资云适配"""
        try:
            context = page_or_frame or self.page
            formatted_date = date_value.replace('/', '-')

            placeholder_map = {
                'startDate': '起始时间',
                'beginDate': '起始时间',
                'endDate': '终止时间',
            }
            placeholder = placeholder_map.get(field_id, field_id)

            selectors = [
                f'input[placeholder*="{placeholder}"]',
                f'.eb-range-input[placeholder*="{placeholder}"]',
                f'input[name="{field_id}"]',
                f'#{field_id}',
                f'.eb-input__inner[placeholder*="{placeholder}"]',
            ]

            if field_id in ('startDate', 'beginDate'):
                selectors.extend([
                    'xpath=//label[@for="startTime"]/following::input[@placeholder="请选择交易日期"][1]',
                    'css=.el-form-item:has(label[for="startTime"]) input.el-input__inner',
                ])
            elif field_id == 'endDate':
                selectors.extend([
                    'xpath=//label[@for="endTime"]/following::input[@placeholder="请选择交易日期"][1]',
                    'css=.el-form-item:has(label[for="endTime"]) input.el-input__inner',
                ])

            input_field = None
            used_selector = None
            for selector in selectors:
                try:
                    elem = context.locator(selector).first
                    if elem.is_visible(timeout=3000):
                        input_field = elem
                        used_selector = selector
                        break
                except Exception:
                    continue

            if not input_field:
                logger.warning(f"{field_id} 元素不存在")
                return False

            logger.info(f"找到日期输入框: {used_selector}")
            input_field.scroll_into_view_if_needed()
            time.sleep(0.3)
            input_field.click()
            time.sleep(0.3)

            # 使用 JS 移除 readonly 并设置值
            input_field.evaluate("""(element, value) => {
                element.removeAttribute('readonly');
                element.removeAttribute('disabled');
                element.value = value;
                element.dispatchEvent(new Event('input', { bubbles: true }));
                element.dispatchEvent(new Event('change', { bubbles: true }));
                element.dispatchEvent(new Event('blur', { bubbles: true }));
            }""", formatted_date)
            time.sleep(0.3)

            actual_value = input_field.input_value()
            logger.info(f"日期设置结果: {field_id} = {actual_value}")
            return True

        except Exception as e:
            logger.error(f"设置 {field_id} 失败: {e}")
            return False

    def _click_query(self) -> bool:
        """点击查询按钮"""
        logger.info("点击查询按钮...")
        try:
            if self._click_button_by_js("查询"):
                logger.info("已点击查询按钮")
                time.sleep(2)
                return True
            logger.warning("未找到查询按钮")
            return False
        except Exception as e:
            logger.error(f"点击查询按钮失败: {e}")
            return False

    # ==================== iframe 与按钮点击工具方法 ====================

    def _get_report_frame(self):
        """定位报表 iframe

        注意：顶部页签可同时打开"银行账户明细维护"和"银行账户明细"两个报表页，
        frames 列表中"维护"iframe 通常排在前面，必须优先匹配明细查询页
        （realUserDetailList / bank_account_details），并排除 Maintenance。
        """
        frames = list(self.page.frames)
        # 第一优先：明确的明细查询页面
        for frame in frames:
            url = frame.url or ''
            if ('realUserDetailList' in url or 'bank_account_details' in url) and 'Maintenance' not in url:
                logger.info(f"找到报表 iframe: {url[:80]}")
                return frame
        # 第二优先：tmsBill 报表 iframe，但排除"维护"页
        for frame in frames:
            url = frame.url or ''
            if ('tms_report_web' in url or 'tmsBill' in url) and 'Maintenance' not in url:
                logger.info(f"找到报表 iframe: {url[:80]}")
                return frame
        for frame in frames:
            url = frame.url or ''
            if frame != self.page.main_frame and url and not url.startswith('chrome-error://'):
                logger.info(f"使用第一个非主 iframe: {url[:80]}")
                return frame
        logger.warning("未找到报表 iframe，使用主页面")
        return self.page

    def _click_button_by_js(self, button_text: str) -> bool:
        """在报表 iframe 中用 JS 查找包含指定文本的可见按钮并点击"""
        frame = self._get_report_frame()
        try:
            clicked = frame.evaluate("""(text) => {
                var buttons = document.querySelectorAll('button, a, input[type="button"]');
                for (var j = 0; j < buttons.length; j++) {
                    var btn = buttons[j];
                    var btnText = (btn.innerText || btn.textContent || btn.value || '').trim();
                    if (btnText === text && btn.offsetParent !== null) {
                        btn.click();
                        return {clicked: true, text: btnText, tag: btn.tagName, match: 'exact'};
                    }
                }
                for (var j = 0; j < buttons.length; j++) {
                    var btn = buttons[j];
                    var btnText = (btn.innerText || btn.textContent || btn.value || '').trim();
                    if (btnText.includes(text) && btn.offsetParent !== null) {
                        btn.click();
                        return {clicked: true, text: btnText, tag: btn.tagName, match: 'contains'};
                    }
                }
                return {clicked: false};
            }""", button_text)
            if clicked and clicked.get('clicked'):
                logger.info(f"JS 点击成功: [{clicked.get('tag')}] '{clicked.get('text')}' ({clicked.get('match')})")
                return True
            logger.warning(f"JS 未找到包含 '{button_text}' 的可见按钮")
            return False
        except Exception as e:
            logger.warning(f"JS 点击 '{button_text}' 失败: {e}")
            return False

    def _click_confirm_ok_by_js(self, context=None) -> bool:
        """点击确认弹窗中的确定按钮
        
        Args:
            context: 指定搜索的 frame/page，为 None 时搜索主页面和所有 iframe
        """
        contexts = [context] if context else [self.page] + list(self.page.frames)
        
        for ctx in contexts:
            try:
                clicked = ctx.evaluate("""() => {
                    var dialogs = document.querySelectorAll('.el-dialog__wrapper, [role="dialog"], .confirm-dialog, .el-message-box__wrapper');
                    for (var d = 0; d < dialogs.length; d++) {
                        var btns = dialogs[d].querySelectorAll('button, .el-button--primary');
                        for (var b = 0; b < btns.length; b++) {
                            var txt = (btns[b].innerText || btns[b].textContent || '').trim();
                            if ((txt.includes('确定') || txt.includes('确认')) && btns[b].offsetParent !== null) {
                                btns[b].click();
                                return {clicked: true, text: txt};
                            }
                        }
                    }
                    var allBtns = document.querySelectorAll('button, .el-button--primary');
                    for (var b = 0; b < allBtns.length; b++) {
                        var txt = (allBtns[b].innerText || allBtns[b].textContent || '').trim();
                        if ((txt.includes('确定') || txt.includes('确认')) && allBtns[b].offsetParent !== null) {
                            allBtns[b].click();
                            return {clicked: true, text: txt};
                        }
                    }
                    return {clicked: false};
                }""")
                if clicked and clicked.get('clicked'):
                    logger.info(f"JS 点击确定按钮成功: '{clicked.get('text')}'")
                    return True
            except Exception as e:
                logger.debug(f"JS 点击确定按钮失败 (context={ctx.url[:50] if hasattr(ctx, 'url') else 'page'}): {e}")
                continue
        
        logger.info("未检测到确认弹窗")
        return False

    def _wait_and_click_confirm(self, max_retries: int = 5, interval: float = 1.0) -> bool:
        """等待并点击确认弹窗"""
        for i in range(max_retries):
            if self._click_confirm_ok_by_js():
                return True
            logger.info(f"等待确认弹窗出现... ({i+1}/{max_retries})")
            time.sleep(interval)
        logger.warning(f"等待 {max_retries} 次后仍未检测到确认弹窗")
        return False

    # ==================== 数据检查与下载 ====================

    def _has_data(self, timeout: int = 30) -> bool:
        """检查查询结果是否有数据

        注意：查询结果表格在报表 iframe（tmsBill）内，Playwright 的 page.locator
        无法穿透 iframe，必须在报表 frame 中检测"暂无数据"。
        查询为异步加载，此处轮询等待结果稳定：出现数据行 -> 有数据；
        出现可见的空状态提示 -> 无数据。
        """
        frame = self._get_report_frame()
        deadline = time.time() + timeout
        last_state = None

        while time.time() < deadline:
            try:
                state = frame.evaluate("""() => {
                    function isVisible(el) {
                        if (!el) return false;
                        var rect = el.getBoundingClientRect();
                        if (rect.width <= 0 || rect.height <= 0) return false;
                        var style = window.getComputedStyle(el);
                        return style.display !== 'none' && style.visibility !== 'hidden';
                    }
                    var loading = Array.from(document.querySelectorAll('.el-loading-mask')).some(isVisible);
                    var rows = Array.from(document.querySelectorAll('.el-table__body tr.el-table__row, table tbody tr.el-table__row')).filter(isVisible).length;
                    var emptyEl = Array.from(document.querySelectorAll('.el-table__empty-text, .el-empty__description, .el-empty-text')).find(isVisible);
                    var emptyText = emptyEl ? (emptyEl.innerText || emptyEl.textContent || '').trim() : '';
                    return {loading: loading, rows: rows, emptyText: emptyText};
                }""")
                last_state = state

                if state.get('loading'):
                    time.sleep(1)
                    continue
                if state.get('rows', 0) > 0:
                    logger.info(f"查询到 {state['rows']} 条记录")
                    return True
                if state.get('emptyText'):
                    logger.info(f"查询结果为空（{state['emptyText']}）")
                    return False
            except Exception as e:
                logger.debug(f"检测查询结果状态失败: {e}")
            time.sleep(1)

        # 超时：按"有数据"处理，走原有下载流程兜底
        logger.warning(f"等待查询结果超时，最后状态: {last_state}，按有数据处理并尝试下载")
        return True

    def _download_receipts(self) -> Optional[str]:
        """下载回单 - 点击'回单下载'按钮并处理确认弹窗"""
        logger.info("下载回单...")

        try:
            before_files = self._get_downloaded_files_before()

            # 步骤1: 点击"回单下载"下拉按钮，展开菜单后点击"回单汇总文件"
            logger.info("步骤1: 点击回单下载下拉菜单 -> 回单汇总文件...")
            download_triggered = False

            # 策略1: Playwright 原生 click（更可靠，会触发完整鼠标事件序列）
            contexts = [self.page] + list(self.page.frames)
            for ctx in contexts:
                if download_triggered:
                    break
                try:
                    selectors = [
                        'button:has-text("回单下载")',
                        'span:has-text("回单下载")',
                        '#cz-button',
                        '.el-dropdown-selfdefine:has-text("回单下载")',
                        '.btn-download .el-button:has-text("回单下载")',
                    ]
                    for sel in selectors:
                        try:
                            elem = ctx.locator(sel).first
                            if elem.count() > 0 and elem.is_visible(timeout=3000):
                                elem.scroll_into_view_if_needed()
                                time.sleep(0.3)
                                elem.click()
                                logger.info(f"Playwright 点击回单下载按钮: {sel}")
                                break
                        except Exception:
                            continue
                    else:
                        continue

                    # 等待下拉菜单展开
                    time.sleep(2)

                    # 在全局页面（主页面 + 所有 iframe）中查找并点击"回单汇总文件"
                    all_contexts = [self.page] + list(self.page.frames)
                    for menu_ctx in all_contexts:
                        if download_triggered:
                            break
                        menu_selectors = [
                            '.el-dropdown-menu__item:has-text("回单汇总文件")',
                            'li:has-text("回单汇总文件")',
                            '.el-popper .el-dropdown-menu__item:has-text("回单汇总文件")',
                            '[id^="dropdown-menu"] .el-dropdown-menu__item:has-text("回单汇总文件")',
                        ]
                        for menu_sel in menu_selectors:
                            try:
                                menu_elem = menu_ctx.locator(menu_sel).first
                                if menu_elem.count() > 0 and menu_elem.is_visible(timeout=5000):
                                    menu_elem.click()
                                    logger.info(f"Playwright 点击回单汇总文件: {menu_sel}")
                                    download_triggered = True
                                    break
                            except Exception:
                                continue
                except Exception as e:
                    logger.debug(f"Playwright 点击回单下载失败: {e}")
                    continue

            # 策略2: JS 兜底
            if not download_triggered:
                logger.warning("Playwright 策略失败，尝试 JS 兜底...")
                for ctx in contexts:
                    if download_triggered:
                        break
                    try:
                        result = ctx.evaluate("""() => {
                            var all = document.querySelectorAll('button, a, span, div');
                            for (var j = 0; j < all.length; j++) {
                                var el = all[j];
                                var txt = (el.innerText || el.textContent || '').trim();
                                if (txt === '回单下载' && el.offsetParent !== null) {
                                    el.click();
                                    return {clicked: true, text: txt};
                                }
                            }
                            return {clicked: false};
                        }""")
                        if result and result.get('clicked'):
                            logger.info(f"JS 点击回单下载按钮成功: '{result.get('text')}'")
                            time.sleep(2)

                            menu_result = self.page.evaluate("""() => {
                                var items = document.querySelectorAll('.el-dropdown-menu__item');
                                for (var i = 0; i < items.length; i++) {
                                    var txt = (items[i].innerText || items[i].textContent || '').trim();
                                    if (txt === '回单汇总文件' && items[i].offsetParent !== null) {
                                        items[i].click();
                                        return {clicked: true};
                                    }
                                }
                                return {clicked: false};
                            }""")
                            if menu_result and menu_result.get('clicked'):
                                logger.info("JS 点击回单汇总文件成功")
                                download_triggered = True
                                break
                    except Exception:
                        continue

            if not download_triggered:
                logger.error("未找到回单下载按钮或回单汇总文件选项")
                return None

            time.sleep(0.5)

            # 步骤2: 等待并点击确认弹窗
            logger.info("步骤2: 等待并点击确认弹窗...")
            confirm_clicked = self._wait_and_click_confirm(max_retries=5, interval=1.0)
            if confirm_clicked:
                logger.info("已点击确认弹窗确定按钮")
            else:
                logger.warning("未检测到确认弹窗，下载可能未触发")

            # 步骤3: 等待并捕获下载文件
            logger.info("步骤3: 等待下载完成...")

            # 优先使用 Playwright expect_download（在点击确定后再监听）
            # 注意：弹窗确定后下载可能已经开始，expect_download 可能捕获不到
            # 因此以文件轮询为主要手段，expect_download 仅作为辅助
            try:
                with self.page.expect_download(timeout=30000) as download_info:
                    # 在监听期间，如果弹窗还没点到，继续尝试
                    if not confirm_clicked:
                        self._wait_and_click_confirm(max_retries=3, interval=0.5)

                download = download_info.value
                temp_path = self.browser_download_path / download.suggested_filename
                download.save_as(str(temp_path))
                logger.info(f"Playwright 下载完成: {temp_path}")

                if temp_path.suffix in ['.html', '.htm']:
                    logger.warning(f"下载的是HTML文件，可能还未生成完成")
                    temp_path.unlink()
                    time.sleep(3)
                    return self._download_receipts()

                return self._move_to_download_path(temp_path)

            except Exception as e:
                logger.warning(f"Playwright download监听失败，使用文件轮询兜底: {e}")

            # 文件轮询兜底：给足够长的时间等待PDF生成和下载
            logger.info("开始文件轮询等待下载...")
            new_file = self._wait_and_find_new_file(before_files, timeout=120)
            if new_file:
                if new_file.suffix in ['.html', '.htm']:
                    logger.warning(f"下载的是HTML文件，可能还未生成完成")
                    new_file.unlink()
                    time.sleep(3)
                    return self._download_receipts()
                return self._move_to_download_path(new_file)

            logger.warning("下载失败：未找到下载文件")
            return None

        except Exception as e:
            logger.error(f"下载回单失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    # ==================== 文件处理 ====================

    def _create_no_data_marker(self) -> str:
        """创建无数据标记文件"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"宁波银行_电子回单_无数据_{timestamp}.txt"
        file_path = self.archive_path / file_name

        content = f"""宁波银行财资云 - 电子回单采集结果
========================================
查询时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
查询日期范围: {self.start_date} 至 {self.end_date}
查询账户: {self.config.get('accounts', []) if self.config.get('accounts') else '默认账户'}

查询结果: 无数据
说明: 在指定的日期范围内未查询到任何电子回单。

可能的原因:
1. 该日期范围内确实没有交易发生
2. 选择的账户在该时间段内无交易
3. 查询条件有误（如日期格式、账户选择等）

建议:
- 检查日期范围是否正确
- 确认账户是否有交易活动
- 尝试扩大查询日期范围

========================================
此文件由自动化程序生成，作为无数据凭证。
"""
        try:
            file_path.write_text(content, encoding='utf-8')
            logger.info(f"已创建无数据标记文件: {file_path}")
            return str(file_path)
        except Exception as e:
            logger.error(f"创建无数据标记文件失败: {e}")
            return None

    def _get_downloaded_files_before(self):
        return get_downloaded_files_before(self.browser_download_path)

    def _wait_and_find_new_file(self, before_files, timeout: int = 60):
        return wait_and_find_new_file(before_files, timeout)

    def _move_to_download_path(self, source_file: Path) -> str:
        """将文件移动到归档目录，按指定格式重命名"""
        timestamp = datetime.now().strftime("%Y%m%d")
        ext = source_file.suffix or ".pdf"

        # 文件名格式: 网银名称_电子回单_yyyymmdd
        file_name = f"宁波银行_电子回单_{timestamp}{ext}"
        file_path = self.archive_path / file_name

        if file_path.exists():
            logger.info(f"文件已存在，将覆盖: {file_path}")

        shutil.move(str(source_file), str(file_path))
        logger.info(f"电子回单已保存: {file_path}")
        return str(file_path)
