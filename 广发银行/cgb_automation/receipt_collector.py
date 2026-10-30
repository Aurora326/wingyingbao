# -*- coding: utf-8 -*-
"""
电子回单采集模块 - 广发银行适配版
"""
import os
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
    """电子回单采集器 - 广发银行适配版"""
    
    def __init__(self, page: Page, config: Dict, statement_dates: Optional[Dict] = None):
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
            # 清理旧数据
            self._cleanup_old_archives()
        else:
            self.archive_path = self.download_path
        
        # 支持打包环境：检测是否在 PyInstaller 环境中运行
        import sys
        if getattr(sys, 'frozen', False):
            # 打包后的 exe 环境，使用 exe 所在目录
            self.browser_download_path = Path(sys.executable).parent / "downloads"
        else:
            # 开发环境，使用脚本所在目录
            self.browser_download_path = Path(__file__).parent / "downloads"
        self.debug_dir = Path(__file__).parent / "debug"
        self.debug_dir.mkdir(exist_ok=True)
    
    def _cleanup_old_archives(self):
        """清理超过保留期限的归档目录"""
        retention_months = self.config.get('retention_months', 2)
        if retention_months <= 0:
            return
        
        # 计算截止日期（当前日期往前推 retention_months 个月）
        cutoff_date = self.current_date.replace(day=1)
        for _ in range(retention_months):
            # 往前推一个月
            if cutoff_date.month == 1:
                cutoff_date = cutoff_date.replace(year=cutoff_date.year - 1, month=12)
            else:
                cutoff_date = cutoff_date.replace(month=cutoff_date.month - 1)
        
        logger.info(f"清理 {retention_months} 个月前的回单归档数据，截止日期: {cutoff_date.strftime('%Y-%m')}")
        
        deleted_count = 0
        
        # 遍历所有年份目录
        for year_dir in self.download_path.iterdir():
            if not year_dir.is_dir():
                continue
            
            try:
                year = int(year_dir.name)
                if year < 2000 or year > 2100:  # 简单的有效性检查
                    continue
            except ValueError:
                continue
            
            # 遍历该年份下的所有月份目录
            for month_dir in year_dir.iterdir():
                if not month_dir.is_dir():
                    continue
                
                try:
                    month = int(month_dir.name)
                    if month < 1 or month > 12:
                        continue
                    
                    # 构建目录日期
                    dir_date = datetime(year, month, 1)
                    
                    # 如果目录日期早于截止日期，删除整个目录
                    if dir_date < cutoff_date:
                        import shutil
                        shutil.rmtree(month_dir)
                        deleted_count += 1
                        logger.info(f"已删除旧归档目录: {year}/{month:02d}")
                except (ValueError, OSError) as e:
                    logger.debug(f"处理目录 {month_dir} 时出错: {e}")
                    continue
        
        # 删除空的年份目录
        for year_dir in self.download_path.iterdir():
            if year_dir.is_dir() and not any(year_dir.iterdir()):
                year_dir.rmdir()
                logger.info(f"已删除空年份目录: {year_dir.name}")
        
        if deleted_count > 0:
            logger.info(f"共清理 {deleted_count} 个旧归档目录")
        else:
            logger.info("没有需要清理的旧归档数据")
    
    def collect(self) -> List[str]:
        """采集电子回单"""
        downloaded_files = []
        logger.info(f"开始采集电子回单: {self.start_date} 至 {self.end_date}")
        
        try:
            # 导航到电子回单页面（带重试）
            nav_success = False
            for retry in range(3):
                if self._navigate_to_receipt_page():
                    nav_success = True
                    break
                time.sleep(2)
            
            if not nav_success:
                logger.error("导航失败")
                return []
            
            # 设置查询条件
            self._set_query_conditions()
            
            # 点击查询
            if not self._click_query():
                logger.warning("点击查询按钮失败")
            
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
        
        logger.info(f"电子回单采集完成: {len(downloaded_files)} 个文件")
        return downloaded_files
    
    def _navigate_to_receipt_page(self) -> bool:
        """导航到电子回单查询页面 - 通过菜单"""
        logger.info("导航到电子回单页面...")
        
        try:
            # 点击"账户查询"菜单
            account_menu = self.page.locator("text=账户查询").first
            if account_menu.count() > 0 and account_menu.is_visible(timeout=3000):
                account_menu.click()
                time.sleep(0.5)
                
                # 点击"电子回单（新）"子菜单
                receipt_new = self.page.locator("text=电子回单（新）").first
                if receipt_new.count() > 0 and receipt_new.is_visible(timeout=3000):
                    receipt_new.click()
                    time.sleep(3)
                    
                    if self._is_receipt_page():
                        logger.info("导航成功")
                        return True
            
            # 备选：通过 onclick 查找
            try:
                receipt_btn = self.page.locator('[onclick*="openDraftLink"]').first
                if receipt_btn.count() > 0 and receipt_btn.is_visible(timeout=3000):
                    receipt_btn.click()
                    time.sleep(3)
                    
                    if self._is_receipt_page():
                        return True
            except Exception:
                pass
            
            logger.error("导航失败")
            return False
            
        except Exception as e:
            logger.error(f"导航失败: {e}")
            return False
    
    def _is_receipt_page(self) -> bool:
        """检查当前是否在电子回单页面"""
        time.sleep(1.5)
        
        contexts = [self.page] + list(self.page.frames)
        
        # 检测主要标志
        indicators = ["text=电子回单（新）", "text=近一年回单", "#queryButton"]
        
        for context in contexts:
            for indicator in indicators:
                try:
                    if context.locator(indicator).first.count() > 0:
                        return True
                except Exception:
                    continue
        
        # 备选：检测账号输入框 + 日期输入框
        for context in contexts:
            try:
                has_account = context.locator("#accountNo1, input[name='accountNo']").count() > 0
                has_date = context.locator("#beginDate").count() > 0
                if has_account and has_date:
                    return True
            except Exception:
                continue
        
        return False
    
    def _select_account(self):
        """选择账号"""
        try:
            time.sleep(0.5)
            
            # 在 page 和所有 frames 中查找账号输入框
            contexts = [self.page] + list(self.page.frames)
            context = None
            
            for ctx in contexts:
                try:
                    elem = ctx.locator("#accountNo1").first
                    if elem.count() > 0 and elem.is_visible(timeout=2000):
                        context = ctx
                        break
                except Exception:
                    continue
            
            if not context:
                return
            
            # 点击输入框展开下拉
            context.locator("#accountNo1").first.click()
            time.sleep(0.3)
            
            # 点击第一个有效账号选项
            try:
                options = context.locator("#accountNo1_dd div[title]").all()
                for opt in options:
                    title = opt.get_attribute("title")
                    if title and "请选择" not in title and len(title) > 10:
                        opt.click()
                        time.sleep(0.3)
                        return
            except Exception:
                pass
                    
        except Exception as e:
            logger.warning(f"选择账号失败: {e}")
    
    def _set_query_conditions(self):
        """设置查询日期条件"""
        logger.info(f"设置日期: {self.start_date} 至 {self.end_date}")
        
        try:
            time.sleep(1)
            self._select_account()
            
            # 查找包含日期输入框的 frame
            context = self.page
            for frame in self.page.frames:
                try:
                    if frame.locator("#beginDate").count() > 0:
                        context = frame
                        break
                except Exception:
                    continue
            
            # 设置日期 - 使用与流水相同的方式
            self._set_date_field_by_playwright("beginDate", self.start_date, context)
            self._set_date_field_by_playwright("endDate", self.end_date, context)
            
            # 关闭日历 - 按 Escape
            self.page.keyboard.press("Escape")
            time.sleep(0.2)
            self.page.keyboard.press("Escape")
            time.sleep(0.2)
            
            # 点击"至"标签关闭日历
            try:
                context.locator("text=至").first.click()
                time.sleep(0.2)
            except Exception:
                pass
                
        except Exception as e:
            logger.warning(f"设置日期失败: {e}")
    
    def _set_date_field_by_playwright(self, field_id: str, date_value: str, page_or_frame=None):
        """设置日期 - 与流水界面一致"""
        try:
            context = page_or_frame or self.page
            input_field = context.locator(f"#{field_id}").first
            
            if input_field.count() == 0:
                return False
            
            input_field.wait_for(state="visible", timeout=5000)
            input_field.scroll_into_view_if_needed()
            time.sleep(0.3)
            
            # 使用 JS 移除 readonly 并设置值
            context.evaluate(f"""
                (function() {{
                    var input = document.getElementById('{field_id}');
                    if (input) {{
                        input.removeAttribute('readonly');
                        input.removeAttribute('disabled');
                        input.value = '{date_value}';
                        ['focus', 'input', 'change'].forEach(function(eventType) {{
                            var event = new Event(eventType, {{ bubbles: true }});
                            input.dispatchEvent(event);
                        }});
                    }}
                }})()
            """)
            time.sleep(0.3)
            return True
            
        except Exception as e:
            logger.error(f"设置 {field_id} 失败: {e}")
            return False
    
    def _click_query(self) -> bool:
        """点击查询按钮"""
        try:
            # 查找查询按钮
            query_context = self.page
            for frame in self.page.frames:
                try:
                    if frame.locator("#queryButton").count() > 0:
                        query_context = frame
                        break
                except Exception:
                    continue
            
            btn = query_context.locator("#queryButton").first
            if btn.count() > 0 and btn.is_visible(timeout=2000):
                btn.click()
                time.sleep(2)
                return True
            
            return False
            
        except Exception as e:
            logger.warning(f"点击查询按钮失败: {e}")
            return False
    
    def _create_no_data_marker(self) -> str:
        """
        创建无数据标记文件
        
        Returns:
            标记文件路径
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"广发银行_电子回单_无数据_{timestamp}.txt"
        # 使用归档路径（按年月分类）
        file_path = self.archive_path / file_name
        
        content = f"""广发银行企业网银 - 电子回单采集结果
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
    
    def _has_data(self) -> bool:
        """检查是否有查询结果"""
        try:
            time.sleep(2)
            
            # 检查空数据提示
            no_data_indicators = [
                "text=暂无数据",
                "text=无数据显示",
                "text=未查询到",
                "text=暂无符合条件的查询结果",
                "#nothing",
                ".no-data",
                ".empty",
                "text=没有记录",
            ]
            for indicator in no_data_indicators:
                try:
                    if self.page.locator(indicator).first.is_visible(timeout=2000):
                        logger.info("查询结果为空")
                        return False
                except Exception:
                    continue
            
            # 检查表格行
            rows = self.page.locator("table tbody tr, .receipt-row, .data-row").all()
            if len(rows) > 0:
                logger.info(f"查询到 {len(rows)} 条记录")
                return True
            
            return True
        except Exception:
            return True
    
    def _select_all_receipts(self):
        """全选回单"""
        try:
            checkbox = self.page.locator("th input[type='checkbox']").first
            if checkbox.count() > 0 and checkbox.is_visible(timeout=2000):
                checkbox.click()
                time.sleep(0.5)
        except Exception:
            pass
    
    def _download_receipts(self) -> Optional[str]:
        """下载回单 - 异步下载，需到下载中心获取"""
        logger.info("下载回单...")
        
        try:
            before_files = self._get_downloaded_files_before()
            
            # 在 page 和所有 frames 中查找下载按钮
            btn = None
            for frame in [self.page] + list(self.page.frames):
                try:
                    elem = frame.locator("#saveAllButtonPdf").first
                    if elem.count() > 0 and elem.is_visible(timeout=2000):
                        btn = elem
                        break
                except Exception:
                    continue
            
            if not btn:
                logger.warning("未找到全量下载按钮")
                return None
            
            # 滚动到视野并点击
            btn.scroll_into_view_if_needed()
            time.sleep(0.5)
            btn.click()
            time.sleep(1.5)
            
            # 选择格式: 直接点击第二个选项（两张电子回单）
            try:
                time.sleep(2)
                for frame in [self.page] + list(self.page.frames):
                    try:
                        options = frame.locator("ul.ltbox2 > li").all()
                        if len(options) >= 2:
                            options[1].click()
                            logger.info("已选择格式（两张）")
                            break
                    except Exception:
                        continue
                time.sleep(1)
            except Exception as e:
                logger.warning(f"选择格式失败: {e}")
            
            # 处理确认对话框
            self._handle_download_dialog()
            
            # 前往下载中心下载文件
            return self._download_from_center(before_files)
            
        except Exception as e:
            logger.error(f"下载回单失败: {e}")
            return None
    
    def _download_from_center(self, before_files: dict) -> Optional[str]:
        """到下载中心下载文件"""
        logger.info("前往下载中心...")
        
        try:
            download_start = time.time()
            
            # 点击下载中心链接
            download_center = self.page.locator("#icon_download, a:has-text('下载中心')").first
            if download_center.count() > 0:
                download_center.click()
                logger.info("已点击下载中心")
                time.sleep(5)  # 增加等待时间
            
            # 等待文件状态从"处理中"变为"成功"
            max_wait = 120
            last_status = ""
            
            # 查找包含下载中心表格的frame - 使用精确的表格ID或tbody ID
            target_frame = None
            for attempt in range(5):  # 重试5次
                for frame in [self.page] + list(self.page.frames):
                    try:
                        # 查找下载中心表格（id="dataTable_1" 或 tbody id="resultTableBody"）
                        has_download_table = frame.evaluate("""
                            () => {
                                var table = document.getElementById('dataTable_1');
                                var tbody = document.getElementById('resultTableBody');
                                var result = {
                                    hasTable: !!(table || tbody),
                                    tableId: table ? 'found' : 'not found',
                                    tbodyId: tbody ? 'found' : 'not found'
                                };
                                return result;
                            }
                        """)
                        if has_download_table.get('hasTable'):
                            target_frame = frame
                            logger.info(f"找到下载中心表格的frame (尝试 {attempt + 1})")
                            logger.info(f"表格状态: table={has_download_table['tableId']}, tbody={has_download_table['tbodyId']}")
                            break
                    except Exception as e:
                        logger.debug(f"Frame evaluate 出错: {e}")
                        continue
                
                if target_frame:
                    break
                
                logger.info(f"未找到表格，等待重试... ({attempt + 1}/5)")
                time.sleep(2)
            
            if not target_frame:
                logger.warning("未找到下载中心表格的frame")
                # 打印所有frame的信息以便调试
                for i, frame in enumerate([self.page] + list(self.page.frames)):
                    try:
                        all_tables = frame.evaluate("""
                            () => {
                                var tables = document.querySelectorAll('table');
                                var info = [];
                                for (var i = 0; i < tables.length; i++) {
                                    var t = tables[i];
                                    var tbody = t.querySelector('tbody');
                                    info.push({
                                        id: t.id || 'no-id',
                                        tbodyId: tbody ? (tbody.id || 'no-tbody-id') : 'no-tbody',
                                        rows: tbody ? tbody.querySelectorAll('tr').length : 0
                                    });
                                }
                                return info;
                            }
                        """)
                        logger.info(f"Frame {i} 表格信息: {all_tables}")
                    except Exception:
                        pass
                return None
            
            # 获取表格信息 - 使用精确的表格选择器
            try:
                table_info = target_frame.evaluate("""
                    (function() {
                        // 优先使用 id="dataTable_1" 或 tbody id="resultTableBody"
                        var table = document.getElementById('dataTable_1');
                        var tbody = document.getElementById('resultTableBody');
                        
                        if (!table && tbody) {
                            table = tbody.closest('table');
                        }
                        
                        if (!table) {
                            return {rowCount: 0, cellCount: 0, error: 'table not found'};
                        }
                        
                        var rows = tbody ? tbody.querySelectorAll('tr') : table.querySelectorAll('tbody tr');
                        if (rows.length == 0) {
                            return {rowCount: 0, cellCount: 0, error: 'no rows'};
                        }
                        
                        var cells = rows[0].querySelectorAll('td');
                        var cellTexts = [];
                        for (var i = 0; i < cells.length; i++) {
                            cellTexts.push((i+1) + ':' + cells[i].innerText.trim().substring(0, 30));
                        }
                        return {
                            rowCount: rows.length,
                            cellCount: cells.length,
                            cells: cellTexts
                        };
                    })()
                """)
                logger.info(f"表格信息: {table_info}")
            except Exception as e:
                logger.warning(f"获取表格信息出错: {e}")
                table_info = {'rowCount': 0}
            
            while time.time() - download_start < max_wait:
                try:
                    # 使用JS获取状态 - 使用精确的表格选择器
                    result = target_frame.evaluate("""
                        (function() {
                            var table = document.getElementById('dataTable_1');
                            var tbody = document.getElementById('resultTableBody');
                            
                            if (!table && tbody) {
                                table = tbody.closest('table');
                            }
                            
                            if (!tbody && table) {
                                tbody = table.querySelector('tbody');
                            }
                            
                            if (!tbody) {
                                return {status: '', error: 'tbody not found'};
                            }
                            
                            var rows = tbody.querySelectorAll('tr');
                            if (rows.length > 0) {
                                var cells = rows[0].querySelectorAll('td');
                                // 状态在第4列（索引3）
                                if (cells.length >= 4) {
                                    var statusText = cells[3].innerText.trim();
                                    return {status: statusText, colIndex: 4, totalCells: cells.length};
                                }
                            }
                            return {status: '', error: 'no rows or cells'};
                        })()
                    """)
                    
                    status = result.get('status', '') if isinstance(result, dict) else str(result)
                    
                    if status and status != last_status:
                        logger.info(f"文件状态: {status}")
                        last_status = status
                    
                    if "成功" in status:
                        logger.info("检测到成功状态，准备点击下载...")
                        
                        # 获取下载链接的 onclick 参数，然后直接执行 doCopy 函数
                        try:
                            # 获取 onclick 属性
                            onclick_info = target_frame.evaluate("""
                                (function() {
                                    var tbody = document.getElementById('resultTableBody');
                                    if (!tbody) return null;
                                    var rows = tbody.querySelectorAll('tr');
                                    if (rows.length > 0) {
                                        var links = rows[0].querySelectorAll('a');
                                        for (var i = 0; i < links.length; i++) {
                                            if (links[i].innerText.trim() === '下载') {
                                                var onclick = links[i].getAttribute('onclick');
                                                return {onclick: onclick, index: i};
                                            }
                                        }
                                    }
                                    return null;
                                })()
                            """)
                            logger.info(f"下载链接 onclick: {onclick_info}")
                            
                            # 直接执行 doCopy 函数
                            if onclick_info and onclick_info.get('onclick'):
                                target_frame.evaluate(onclick_info['onclick'])
                                logger.info("已执行 doCopy 函数")
                                time.sleep(2)  # 等待对话框弹出
                                # 处理确认对话框
                                self._handle_download_dialog()
                                time.sleep(10)  # 等待文件下载
                            else:
                                # 备选：直接点击链接
                                download_link = target_frame.locator("#resultTableBody tr:first-child td:last-child a").first
                                if download_link.count() > 0:
                                    download_link.click()
                                    logger.info("已点击下载链接")
                                    time.sleep(2)
                                    # 处理确认对话框
                                    self._handle_download_dialog()
                                    time.sleep(10)
                                
                        except Exception as click_err:
                            logger.warning(f"点击下载失败: {click_err}")
                            import traceback
                            logger.error(traceback.format_exc())
                        
                        break
                    elif "处理中" in status:
                        logger.info("文件处理中，等待...")
                        # 使用JS点击刷新
                        try:
                            refreshed = target_frame.evaluate("""
                                (function() {
                                    var refreshDiv = document.querySelector('div.yegj_an1_c');
                                    if (refreshDiv) {
                                        refreshDiv.click();
                                        return 'div clicked';
                                    }
                                    if (typeof doRefresh === 'function') {
                                        doRefresh();
                                        return 'function called';
                                    }
                                    return 'not found';
                                })()
                            """)
                            logger.info(f"刷新结果: {refreshed}")
                        except Exception as refresh_err:
                            logger.debug(f"刷新出错: {refresh_err}")
                    else:
                        logger.debug(f"未知状态: {status}")
                except Exception as e:
                    logger.warning(f"检查状态出错: {e}")
                
                time.sleep(5)
            
            # 等待文件下载完成 - 增加重试次数
            new_file = None
            for attempt in range(3):
                new_file = self._wait_and_find_new_file(before_files, timeout=60, start_time=download_start)
                if new_file:
                    break
                logger.info(f"第 {attempt+1} 次尝试未找到文件，继续等待...")
                time.sleep(10)
            
            # 如果还是没找到，尝试手动点击下载按钮（通过浏览器原生点击）
            if not new_file:
                logger.warning("自动下载未找到文件，尝试通过浏览器原生事件点击...")
                try:
                    # 使用 page.mouse 点击
                    link_box = target_frame.locator("#resultTableBody tr:first-child td:last-child a").first.bounding_box()
                    if link_box:
                        self.page.mouse.click(link_box['x'] + link_box['width']/2, link_box['y'] + link_box['height']/2)
                        logger.info("已使用鼠标点击下载链接")
                        time.sleep(15)
                        new_file = self._wait_and_find_new_file(before_files, timeout=60, start_time=time.time())
                except Exception as mouse_err:
                    logger.warning(f"鼠标点击失败: {mouse_err}")
            
            if new_file:
                return self._move_to_download_path(new_file)
            
            return None
            
        except Exception as e:
            logger.error(f"下载中心下载失败: {e}")
            return None
    
    def _handle_download_dialog(self):
        """处理下载确认对话框"""
        try:
            time.sleep(1)
            
            # 检查确认对话框（标题: ebanks.cgbchina.com.cn 显示，内容: 是否确认下载该文件？）
            contexts = [self.page] + list(self.page.frames)
            for ctx in contexts:
                try:
                    # 查找包含"是否确认下载"文本的对话框
                    confirm_text = ctx.locator("text=是否确认下载该文件").first
                    if confirm_text.count() > 0 and confirm_text.is_visible(timeout=2000):
                        logger.info("发现下载确认对话框")
                        # 点击确定按钮
                        ok_btn = ctx.locator("button:has-text('确定')").first
                        if ok_btn.count() > 0:
                            ok_btn.click()
                            logger.info("已点击确定按钮")
                            time.sleep(2)
                            return True
                except Exception:
                    continue
            
            # 备选：通用确定按钮
            for ctx in contexts:
                try:
                    btn = ctx.locator("button:has-text('确定')").first
                    if btn.count() > 0 and btn.is_visible(timeout=2000):
                        btn.click()
                        logger.info("已点击通用确定按钮")
                        time.sleep(1)
                        return True
                except Exception:
                    continue
            
            return True
            
        except Exception as e:
            logger.warning(f"处理对话框失败: {e}")
            return False
    
    def _get_downloaded_files_before(self):
        return get_downloaded_files_before(self.browser_download_path)

    def _wait_and_find_new_file(self, before_files, timeout: int = 60):
        return wait_and_find_new_file(before_files, timeout)

    def _move_to_download_path(self, source_file: Path) -> str:
        """将文件移动到归档目录，按指定格式重命名"""
        timestamp = datetime.now().strftime("%Y%m%d")
        ext = source_file.suffix or ".pdf"
        
        # 文件名格式: 网银名称_电子回单_yyyymmdd
        file_name = f"广发银行_电子回单_{timestamp}{ext}"
        # 使用归档路径（按年月分类）
        file_path = self.archive_path / file_name
        
        if file_path.exists():
            logger.info(f"文件已存在，将覆盖: {file_path}")
        
        shutil.move(str(source_file), str(file_path))
        logger.info(f"电子回单已保存: {file_path}")
        return str(file_path)
