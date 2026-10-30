# -*- coding: utf-8 -*-
"""
电子回单采集模块 - 宁波银行适配版
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
    """电子回单采集器 - 宁波银行适配版"""
    
    def __init__(self, page: Page, config: Dict, statement_dates: Optional[Dict] = None, browser_download_path: Optional[Path] = None):
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
        
        # 浏览器下载路径（优先使用外部传入的路径，与 base_browser 保持一致）
        if browser_download_path:
            self.browser_download_path = Path(browser_download_path)
            logger.info(f"使用传入的浏览器下载路径: {self.browser_download_path}")
        else:
            import sys
            if getattr(sys, 'frozen', False):
                self.browser_download_path = Path(sys.executable).parent / "downloads"
                logger.info(f"[打包环境] 浏览器下载路径: {self.browser_download_path}")
            else:
                self.browser_download_path = Path(__file__).parent / "downloads"
                logger.info(f"[开发环境] 浏览器下载路径: {self.browser_download_path}")
    
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
                        import shutil
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
    
    def collect(self) -> List[str]:
        """采集电子回单"""
        downloaded_files = []
        logger.info(f"开始采集电子回单: {self.start_date} 至 {self.end_date}")
        
        try:
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
        """导航到电子回单查询页面 - 宁波银行适配"""
        logger.info("导航到电子回单页面...")
        
        try:
            # 方式1: 直接通过 URL 导航到明细查询页面（回单在同一页面）
            logger.info("方式1: 直接通过 URL 导航...")
            base_url = "https://cb.nbcb.com.cn/microbase/microhome/index.html"
            detail_url = f"{base_url}#/entfinbank-account2-web/010102"
            self.page.goto(detail_url, wait_until="networkidle", timeout=30000)
            time.sleep(3)
            
            if self._is_receipt_page():
                logger.info("✓ 导航成功（通过直接URL）")
                return True
            
            # 方式2: 点击菜单导航
            logger.info("方式2: 尝试通过菜单导航...")
            account_menu = self.page.locator("text=账户查询, text=账户管理, .account-menu").first
            if account_menu.count() > 0 and account_menu.is_visible(timeout=3000):
                account_menu.click()
                time.sleep(0.5)
                
                # 点击"账户明细查询"
                receipt_menu = self.page.locator("text=账户明细查询, text=明细查询, .detail-query").first
                if receipt_menu.count() > 0 and receipt_menu.is_visible(timeout=3000):
                    receipt_menu.click()
                    time.sleep(3)
                    
                    if self._is_receipt_page():
                        logger.info("✓ 导航成功（通过菜单）")
                        return True
            
            # 方式3: 通过 JavaScript
            try:
                logger.info("方式3: 通过 JavaScript 导航...")
                self.page.evaluate("""() => {
                    window.location.hash = '#/entfinbank-account2-web/010102';
                }""")
                time.sleep(3)
                if self._is_receipt_page():
                    logger.info("✓ 导航成功（通过JS）")
                    return True
            except Exception:
                pass
            
            logger.error("导航失败")
            return False
            
        except Exception as e:
            logger.error(f"导航失败: {e}")
            return False
    
    def _is_receipt_page(self) -> bool:
        """检查当前是否在回单/明细查询页面 - 宁波银行适配"""
        time.sleep(1.5)
        
        contexts = [self.page] + list(self.page.frames)
        
        # 宁波银行回单在明细查询页面
        indicators = [
            "text=账户明细查询",
            "text=交易明细查询", 
            "text=明细查询",
            'input[placeholder*="查询起始日期"]',  # 宁波银行日期输入框
            'input[placeholder*="查询终止日期"]',
            'button:has-text("批量下载回单")',  # 回单下载按钮
            '.eb-button--default',  # 宁波银行按钮样式
        ]
        
        for context in contexts:
            for indicator in indicators:
                try:
                    if context.locator(indicator).first.count() > 0:
                        logger.info(f"检测到回单/明细页面: {indicator}")
                        return True
                except Exception:
                    continue
        
        # 备选：检测账号输入框 + 日期输入框
        for context in contexts:
            try:
                has_account = context.locator('input[name="account"], select[name="account"]').count() > 0
                has_date = context.locator('input[placeholder*="查询起始日期"]').count() > 0
                if has_account and has_date:
                    return True
            except Exception:
                continue
        
        return False
    
    def _select_account(self):
        """选择账号"""
        try:
            time.sleep(0.5)
            
            contexts = [self.page] + list(self.page.frames)
            context = None
            
            for ctx in contexts:
                try:
                    elem = ctx.locator('select[name="account"], #account, .account-select').first
                    if elem.count() > 0 and elem.is_visible(timeout=2000):
                        context = ctx
                        break
                except Exception:
                    continue
            
            if not context:
                return
            
            # 点击输入框展开下拉
            account_select = context.locator('select[name="account"], #account, .account-select').first
            account_select.click()
            time.sleep(0.3)
            
            # 点击第一个有效账号选项
            try:
                options = context.locator("option, .dropdown-item, .select-option").all()
                for opt in options:
                    text = opt.text_content()
                    if text and "请选择" not in text and len(text) > 10:
                        opt.click()
                        time.sleep(0.3)
                        return
            except Exception:
                pass
                    
        except Exception as e:
            logger.warning(f"选择账号失败: {e}")
    
    def _set_query_conditions(self):
        """设置查询日期条件 - 宁波银行适配"""
        logger.info(f"设置日期: {self.start_date} 至 {self.end_date}")
        
        try:
            time.sleep(1)
            self._select_account()
            
            # 查找包含日期输入框的 frame
            context = self.page
            for frame in self.page.frames:
                try:
                    if frame.locator('input[placeholder*="查询起始日期"]').count() > 0:
                        context = frame
                        break
                except Exception:
                    continue
            
            # 设置日期（宁波银行适配）
            self._set_date_field_by_playwright("startDate", self.start_date, context)
            self._set_date_field_by_playwright("beginDate", self.start_date, context)
            self._set_date_field_by_playwright("endDate", self.end_date, context)
            
            # 关闭日历
            self.page.keyboard.press("Escape")
            time.sleep(0.2)
            self.page.keyboard.press("Escape")
            time.sleep(0.2)
            
            # 点击空白处关闭日历
            try:
                context.locator("text=至, .date-separator, .eb-range-separator").first.click()
                time.sleep(0.2)
            except Exception:
                pass
                
        except Exception as e:
            logger.warning(f"设置日期失败: {e}")
    
    def _set_date_field_by_playwright(self, field_id: str, date_value: str, page_or_frame=None):
        """设置日期 - 宁波银行适配"""
        try:
            context = page_or_frame or self.page
            
            # 根据 field_id 映射到宁波银行的 placeholder
            placeholder_map = {
                'startDate': '查询起始日期',
                'beginDate': '查询起始日期',
                'endDate': '查询终止日期',
            }
            placeholder = placeholder_map.get(field_id, field_id)
            
            selectors = [
                f'input[placeholder*="{placeholder}"]',  # placeholder 匹配
                f'.eb-range-input[placeholder*="{placeholder}"]',  # 日期范围输入框
                f'input[name="{field_id}"]',
                f'#{field_id}',
            ]
            
            input_field = None
            for selector in selectors:
                try:
                    elem = context.locator(selector).first
                    if elem.count() > 0 and elem.is_visible(timeout=2000):
                        input_field = elem
                        break
                except Exception:
                    continue
            
            if not input_field:
                logger.warning(f"{field_id} 元素不存在")
                return False
            
            input_field.scroll_into_view_if_needed()
            time.sleep(0.3)
            
            # 先点击激活
            input_field.click()
            time.sleep(0.3)
            
            # 使用 JS 设置值
            input_field.evaluate(f"""(element) => {{
                element.removeAttribute('readonly');
                element.removeAttribute('disabled');
                element.value = '{date_value}';
                element.dispatchEvent(new Event('input', {{ bubbles: true }}));
                element.dispatchEvent(new Event('change', {{ bubbles: true }}));
                element.dispatchEvent(new Event('blur', {{ bubbles: true }}));
            }}""")
            time.sleep(0.3)
            
            # 验证设置
            actual_value = input_field.input_value()
            logger.info(f"日期设置结果: {field_id} = {actual_value}")
            return True
            
        except Exception as e:
            logger.error(f"设置 {field_id} 失败: {e}")
            return False
    
    def _click_query(self) -> bool:
        """点击查询按钮 - 宁波银行适配"""
        try:
            query_context = self.page
            for frame in self.page.frames:
                try:
                    if frame.locator('button:has-text("查询")').count() > 0:
                        query_context = frame
                        break
                except Exception:
                    continue
            
            # 宁波银行查询按钮选择器
            query_selectors = [
                'button.eb-button--default:has-text("查询")',
                'button:has(.eb-icon-search)',
                'button:has-text("查询")',
            ]
            
            for selector in query_selectors:
                try:
                    btn = query_context.locator(selector).first
                    if btn.count() > 0 and btn.is_visible(timeout=2000):
                        btn.scroll_into_view_if_needed()
                        time.sleep(0.3)
                        btn.click()
                        logger.info(f"已点击查询按钮: {selector}")
                        time.sleep(2)
                        return True
                except Exception:
                    continue
            
            return False
            
        except Exception as e:
            logger.warning(f"点击查询按钮失败: {e}")
            return False
    
    def _create_no_data_marker(self) -> str:
        """创建无数据标记文件"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"宁波银行_电子回单_无数据_{timestamp}.txt"
        file_path = self.archive_path / file_name
        
        content = f"""宁波银行企业网银 - 电子回单采集结果
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
    
    def _download_receipts(self) -> Optional[str]:
        """下载回单 - 宁波银行适配"""
        logger.info("下载回单...")
        
        try:
            before_files = self._get_downloaded_files_before()
            
            # 步骤0: 设置每页显示100条（在勾选前执行）
            logger.info("步骤0: 设置每页显示100条...")
            try:
                # 点击分页下拉框
                page_size_selectors = [
                    '.eb-select.eb-select--mini',  # 分页下拉框
                    '.eb-pagination__sizes .eb-select',  # 分页大小选择器
                    'input[placeholder="请选择"]',  # 占位符匹配
                    '.eb-select-dropdown__item',  # 下拉选项
                ]
                
                # 先点击下拉框展开
                dropdown_clicked = False
                for selector in page_size_selectors:
                    try:
                        dropdown = self.page.locator(selector).first
                        if dropdown.count() > 0 and dropdown.is_visible(timeout=2000):
                            dropdown.click()
                            logger.info(f"已点击分页下拉框: {selector}")
                            dropdown_clicked = True
                            time.sleep(1)
                            break
                    except Exception:
                        continue
                
                if dropdown_clicked:
                    # 点击"100"选项
                    try:
                        option = self.page.locator('.eb-select-dropdown__item:has-text("100"), li:has-text("100"), span:has-text("100")').first
                        if option.count() > 0 and option.is_visible(timeout=2000):
                            option.click()
                            logger.info("已选择每页100条")
                            time.sleep(2)  # 等待页面刷新
                        else:
                            # 尝试使用键盘输入
                            self.page.keyboard.type("100")
                            time.sleep(0.5)
                            self.page.keyboard.press("Enter")
                            logger.info("已通过键盘输入100")
                            time.sleep(2)
                    except Exception as e:
                        logger.warning(f"选择100选项失败: {e}")
                else:
                    logger.warning("未找到分页下拉框")
            except Exception as e:
                logger.warning(f"设置分页大小失败: {e}")
            
            # 步骤1: 勾选数据（点击表格第一行的复选框）
            logger.info("步骤1: 勾选交易数据...")
            checkbox_selected = False
            
            # 尝试多种方式勾选
            checkbox_selectors = [
                '.eb-checkbox__original',  # 宁波银行复选框
                '.eb-v2-table-column--selection input[type="checkbox"]',  # 表格选择列
                '.el-table__row:first-child .el-checkbox__input',  # 第一行复选框
                'table tbody tr:first-child input[type="checkbox"]',  # 通用表格第一行
                '.eb-checkbox__inner',  # 复选框内部元素
            ]
            
            for selector in checkbox_selectors:
                try:
                    # 尝试找到复选框并点击
                    checkbox = self.page.locator(selector).first
                    if checkbox.count() > 0 and checkbox.is_visible(timeout=2000):
                        checkbox.scroll_into_view_if_needed()
                        time.sleep(0.3)
                        checkbox.click()
                        logger.info(f"已点击复选框: {selector}")
                        checkbox_selected = True
                        time.sleep(1)
                        break
                except Exception as e:
                    logger.debug(f"复选框选择器 {selector} 失败: {e}")
                    continue
            
            if not checkbox_selected:
                # 尝试点击表头全选
                try:
                    header_checkbox = self.page.locator('th.eb-v2-table-column--selection .eb-checkbox__original, th .eb-checkbox__input').first
                    if header_checkbox.count() > 0 and header_checkbox.is_visible(timeout=2000):
                        header_checkbox.click()
                        logger.info("已点击表头全选复选框")
                        checkbox_selected = True
                        time.sleep(1)
                except Exception:
                    pass
            
            if not checkbox_selected:
                logger.warning("未能勾选任何数据，继续尝试下载...")
            
            # 步骤2: 点击"批量下载回单"按钮
            logger.info("步骤2: 点击批量下载回单按钮...")
            btn = None
            
            # 宁波银行批量下载回单按钮选择器
            download_btn_selectors = [
                'button:has-text("批量下载回单")',  # 主要按钮
                'button:has(.eb-icon-batch_download)',  # 有批量下载图标的按钮
                '.eb-button:has-text("批量下载回单")',
                'button[data-v-67e362e0]:has-text("批量下载回单")',  # 带data属性的按钮
                'span:has-text("批量下载回单")',  # span文字
            ]
            
            for selector in download_btn_selectors:
                try:
                    elem = self.page.locator(selector).first
                    if elem.count() > 0 and elem.is_visible(timeout=3000):
                        btn = elem
                        logger.info(f"找到批量下载回单按钮: {selector}")
                        break
                except Exception:
                    continue
            
            if not btn:
                logger.warning("未找到批量下载回单按钮，尝试通用下载按钮...")
                # 尝试通用下载按钮
                for frame in [self.page] + list(self.page.frames):
                    try:
                        elem = frame.locator('button:has-text("下载"), button:has-text("导出"), #downloadBtn, .download-btn').first
                        if elem.count() > 0 and elem.is_visible(timeout=2000):
                            btn = elem
                            break
                    except Exception:
                        continue
            
            if not btn:
                logger.error("未找到任何下载按钮")
                return None
            
            # 滚动到视野并点击
            btn.scroll_into_view_if_needed()
            time.sleep(0.5)
            
            # 使用 Playwright 下载监听
            try:
                with self.page.expect_download(timeout=60000) as download_info:
                    btn.click()
                download = download_info.value
                temp_path = self.browser_download_path / download.suggested_filename
                download.save_as(str(temp_path))
                logger.info(f"Playwright 下载完成: {temp_path}")
                
                # 等待文件稳定
                time.sleep(1)
                return self._move_to_download_path(temp_path)
            except Exception as e:
                logger.warning(f"Playwright download监听失败，使用文件轮询备选: {e}")
                logger.info("已点击下载按钮，等待下载...")
                time.sleep(5)
                
                # 步骤3: 等待文件下载完成
                new_file = self._wait_and_find_new_file(before_files, timeout=60)
                if new_file:
                    return self._move_to_download_path(new_file)
            
            return None
            
        except Exception as e:
            logger.error(f"下载回单失败: {e}")
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
