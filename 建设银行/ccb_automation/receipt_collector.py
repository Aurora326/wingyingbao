# -*- coding: utf-8 -*-
"""
电子回单采集模块 - 建设银行适配版

注意：建设银行页面结构可能因版本更新而变化，
如果采集失败，请检查页面元素选择器是否需要更新。
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
    """电子回单采集器 - 建设银行适配版"""
    
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
    
    def _find_frame_containing(self, selectors: list, timeout: int = 10):
        """在所有 frame（包括 iframe）中查找包含任一选择器的 frame"""
        start = time.time()
        while time.time() - start < timeout:
            for frame in self.page.frames:
                try:
                    for selector in selectors:
                        if frame.locator(selector).count() > 0:
                            return frame
                except Exception:
                    continue
            time.sleep(0.5)
        return None
    
    def _to_ccb_date(self, date_str: str) -> str:
        """将 YYYY-MM-DD 转换为 CCB 需要的 YYYYMMDD"""
        return date_str.replace('-', '')
    
    def _set_date_field_ccb(self, frame, selector: str, value: str):
        """在 iframe 中设置日期输入框"""
        input_field = frame.locator(selector).first
        input_field.wait_for(state="visible", timeout=5000)
        
        frame.evaluate("""
            (args) => {
                var input = document.querySelector(args.selector);
                if (input) {
                    input.value = args.value;
                    input.removeAttribute('readonly');
                    input.removeAttribute('disabled');
                    ['focus', 'input', 'change', 'blur'].forEach(function(type) {
                        var evt = new Event(type, { bubbles: true });
                        input.dispatchEvent(evt);
                    });
                }
            }
        """, {"selector": selector, "value": value})
        time.sleep(0.3)
    
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
    
    def _close_auxiliary_pages(self):
        """关闭所有非主页面的辅助标签页"""
        try:
            current_page = self.page
            for page in list(self.page.context.pages):
                try:
                    if page == current_page:
                        continue
                    url = page.url or ""
                    logger.info(f"关闭辅助页面: {url}")
                    page.close()
                except Exception:
                    continue
            # 确保回到主页面
            try:
                current_page.bring_to_front()
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"关闭辅助页面失败: {e}")

    def collect(self) -> List[str]:
        """采集电子回单 - CCB在交易明细结果页勾选后预览打印"""
        downloaded_files = []
        logger.info(f"开始采集电子回单: {self.start_date} 至 {self.end_date}")
        
        # 先关闭流水下载产生的辅助页面
        self._close_auxiliary_pages()
        
        try:
            # 确保在交易明细结果页
            self._ensure_on_result_page()
            
            # 下载回单（勾选 → 预览 → 保存PDF）
            file_path = self._download_receipts()
            if file_path:
                downloaded_files.append(file_path)
                    
        except Exception as e:
            logger.error(f"采集电子回单出错: {e}")
            raise
        
        logger.info(f"电子回单采集完成: {len(downloaded_files)} 个文件")
        return downloaded_files
    
    def _ensure_on_result_page(self):
        """确保当前在交易明细结果页，如果不在则执行查询"""
        if self._is_statement_result_page():
            logger.info("已在交易明细结果页")
            return
        
        logger.info("当前不在交易明细结果页，执行查询...")
        
        # Step 1: 点击账户查询
        frame = self._find_frame_containing(['#toAccQuery'], timeout=10)
        if not frame:
            raise Exception("未找到账户查询链接")
        frame.locator('#toAccQuery').first.click()
        time.sleep(3)
        
        # Step 2: 全选账户
        frame = self._find_frame_containing(['#all_0'], timeout=10)
        if not frame:
            raise Exception("未进入账户列表页")
        all_cb = frame.locator('#all_0').first
        if all_cb.count() > 0:
            try:
                if not all_cb.is_checked():
                    all_cb.check()
            except Exception:
                all_cb.click()
        time.sleep(1)
        
        # Step 3: 点击交易明细
        detail_btn = frame.locator('input[name="Submit3"]').first
        if detail_btn.count() == 0:
            detail_btn = frame.locator('input[value="交易明细"]').first
        if detail_btn.count() == 0:
            raise Exception("未找到交易明细按钮")
        detail_btn.click()
        time.sleep(3)
        
        # Step 4: 设置日期并查询
        frame = self._find_frame_containing(['#StDt', '#EdDt'], timeout=10)
        if not frame:
            raise Exception("未进入交易明细查询页")
        
        start_ccb = self._to_ccb_date(self.start_date)
        end_ccb = self._to_ccb_date(self.end_date)
        self._set_date_field_ccb(frame, '#StDt', start_ccb)
        self._set_date_field_ccb(frame, '#EdDt', end_ccb)
        time.sleep(0.5)
        
        # 点击确定
        confirm_btn = frame.locator('input[type="submit"][name="Submit1"]').first
        if confirm_btn.count() == 0:
            confirm_btn = frame.locator('input[value="确定"]').first
        if confirm_btn.count() == 0:
            raise Exception("未找到确定按钮")
        confirm_btn.click()
        time.sleep(5)
        
        # 等待结果页
        result_frame = self._find_frame_containing(['#dlAll', 'input[name="all"]'], timeout=15)
        if not result_frame:
            raise Exception("查询结果页加载失败")
        
        logger.info("已进入交易明细结果页")
    
    def _is_statement_result_page(self) -> bool:
        """检查当前是否在交易明细结果页"""
        frame = self._find_frame_containing(['#dlAll', 'input[name="all"]'], timeout=3)
        return frame is not None
    
    def _select_account(self):
        """选择账号"""
        try:
            time.sleep(0.5)
            
            # 在 page 和所有 frames 中查找账号输入框
            contexts = [self.page] + list(self.page.frames)
            context = None
            
            for ctx in contexts:
                try:
                    elem = ctx.locator("#accountNo, #accountNo1, #acctNo").first
                    if elem.count() > 0 and elem.is_visible(timeout=2000):
                        context = ctx
                        break
                except Exception:
                    continue
            
            if not context:
                return
            
            # 点击输入框展开下拉
            context.locator("#accountNo, #accountNo1, #acctNo").first.click()
            time.sleep(0.3)
            
            # 点击第一个有效账号选项
            try:
                options = context.locator(
                    "#accountNo_dd div[title], .select-option, .dropdown-item, .el-select-dropdown__item"
                ).all()
                for opt in options:
                    title = opt.get_attribute("title") or opt.text_content()
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
                    if frame.locator("#beginDate, #startDate").count() > 0:
                        context = frame
                        break
                except Exception:
                    continue
            
            # 设置日期
            begin_field = "beginDate" if context.locator("#beginDate").count() > 0 else "startDate"
            end_field = "endDate"
            
            self._set_date_field_by_playwright(begin_field, self.start_date, context)
            self._set_date_field_by_playwright(end_field, self.end_date, context)
            
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
                input_field = context.locator(f"input[name='{field_id}']").first
            
            if input_field.count() == 0:
                return False
            
            input_field.wait_for(state="visible", timeout=5000)
            input_field.scroll_into_view_if_needed()
            time.sleep(0.3)
            
            # 使用 JS 移除 readonly 并设置值
            context.evaluate("""
                (function(args) {
                    var input = document.getElementById(args.field_id);
                    if (!input) {
                        input = document.querySelector('input[name="' + args.field_id + '"]');
                    }
                    if (input) {
                        input.removeAttribute('readonly');
                        input.removeAttribute('disabled');
                        input.value = args.date_value;
                        ['focus', 'input', 'change'].forEach(function(eventType) {
                            var event = new Event(eventType, { bubbles: true });
                            input.dispatchEvent(event);
                        });
                    }
                })()
            """, {"field_id": field_id, "date_value": date_value})
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
                    if frame.locator("#queryButton, button:has-text('查询')").count() > 0:
                        query_context = frame
                        break
                except Exception:
                    continue
            
            btn = query_context.locator("#queryButton").first
            if btn.count() > 0 and btn.is_visible(timeout=2000):
                btn.click()
                time.sleep(2)
                return True
            
            # 尝试其他查询按钮选择器
            for selector in ["button:has-text('查询')", "input[value='查询']", ".query-btn"]:
                btn = query_context.locator(selector).first
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
        file_name = f"建设银行_电子回单_无数据_{timestamp}.txt"
        # 使用归档路径（按年月分类）
        file_path = self.archive_path / file_name
        
        content = f"""建设银行企业网银 - 电子回单采集结果
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
                "text=没有符合条件的记录",
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
        """下载电子回单 - 勾选记录后预览，选择一页打2个，保存为PDF"""
        logger.info("开始下载电子回单...")
        
        # Step 1: 在结果页勾选所有记录
        frame = self._find_frame_containing(['input[name="all"]'], timeout=10)
        if not frame:
            raise Exception("未找到交易明细结果页")
        
        all_cb = frame.locator('input[name="all"]').first
        if all_cb.count() > 0:
            try:
                if not all_cb.is_checked():
                    all_cb.check()
            except Exception:
                all_cb.click()
        time.sleep(1)
        
        # Step 2: 点击"电子回执打印预览"
        preview_btn = frame.locator('input[value="电子回执打印/预览"]').first
        if preview_btn.count() == 0:
            preview_btn = frame.locator('input[onclick*="EBPrint"]').first
        if preview_btn.count() == 0:
            preview_btn = frame.locator('input[name="Submit1"]').first
        
        if preview_btn.count() == 0:
            raise Exception("未找到电子回执打印预览按钮")
        
        logger.info("点击电子回执打印预览...")
        preview_btn.click()
        time.sleep(5)
        
        # Step 3: 等待回单预览页加载
        # 判断条件：页面上没有"电子回执打印/预览"按钮（说明已进入预览模式）
        # 同时有多个回单
        logger.info("等待回单预览页加载...")
        preview_frame = None
        start = time.time()
        while time.time() - start < 15:
            for frame in self.page.frames:
                try:
                    # 回单预览页会有多个回单，且没有"电子回执打印/预览"按钮
                    receipt_count = frame.locator('text=中国建设银行网上银行电子回执').count()
                    has_preview_btn = frame.locator('input[value="电子回执打印/预览"]').count() > 0
                    if receipt_count >= 2 and not has_preview_btn:
                        preview_frame = frame
                        logger.info(f"回单预览页已加载，找到 {receipt_count} 个回单，无预览按钮")
                        break
                except Exception:
                    continue
            if preview_frame:
                break
            time.sleep(0.5)
        
        if not preview_frame:
            raise Exception("回单预览页加载失败")
        
        # Step 4: 滚动到页面底部，确保所有回单都加载渲染
        logger.info("滚动预览页加载所有回单...")
        try:
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
        time.sleep(2)
        
        # Step 5: 悬浮并点击"回执打印"下拉按钮
        logger.info("悬浮并点击回执打印下拉...")
        preview_frame.evaluate("""
            () => {
                const printBtn = document.querySelector('#print') 
                    || document.querySelector('.tableRBtn.print')
                    || document.querySelector('a:has-text("回执打印")');
                if (printBtn) {
                    printBtn.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
                    printBtn.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
                }
            }
        """)
        time.sleep(1)
        
        preview_frame.evaluate("""
            () => {
                const printBtn = document.querySelector('#print') 
                    || document.querySelector('.tableRBtn.print')
                    || document.querySelector('a:has-text("回执打印")');
                if (printBtn) printBtn.click();
            }
        """)
        time.sleep(1)
        
        # Step 6: 先获取 iframe 的 HTML（此时还没点击"一页打2个"，不会被 window.print() 阻塞）
        logger.info("获取 iframe 中的回单预览内容...")
        try:
            # 先用 content() 读取 DOM，它走 CDP 的 DOM.getDocument，不受 JS 阻塞影响
            html = preview_frame.content()
            logger.info(f"获取到 HTML 长度: {len(html)}")
        except Exception as e:
            logger.error(f"获取 iframe HTML 失败: {e}")
            raise
        
        # Step 7: 选择"一页打2个"（异步点击，避免 evaluate 被 window.print() 阻塞）
        logger.info("选择一页打2个...")
        click_result = preview_frame.evaluate("""
            () => {
                const allElements = document.querySelectorAll('a, li, span, div');
                for (const el of allElements) {
                    const text = el.textContent.trim().replace(/\\s+/g, '');
                    if (text === '一页打2个') {
                        // 异步点击，避免 window.print() 阻塞 evaluate 返回
                        setTimeout(() => el.click(), 100);
                        return 'clicked_async:' + el.tagName;
                    }
                }
                return 'not_found';
            }
        """)
        logger.info(f"选择一页打2个结果: {click_result}")
        time.sleep(3)  # 等待打印对话框弹出
        
        # 创建新页面，先导航到 iframe 的 URL（让相对路径的 CSS/图片能正确加载）
        logger.info("创建新页面用于打印...")
        new_page = self.page.context.new_page()
        try:
            # 先 goto 到 iframe 的 URL，让浏览器建立正确的 base URL
            try:
                new_page.goto(preview_frame.url, wait_until="domcontentloaded", timeout=10000)
            except Exception:
                pass  # 可能无法直接加载，没关系，set_content 会覆盖
            
            # 设置 iframe 的 HTML 内容
            new_page.set_content(html, wait_until="networkidle", timeout=15000)
            time.sleep(2)
            
            # 先截图看看新页面渲染效果
            screenshot_path = self.browser_download_path / "receipt_preview.png"
            new_page.screenshot(path=str(screenshot_path), full_page=True)
            logger.info(f"已保存预览截图: {screenshot_path}")
            
            # 在新页面上调用 pdf()
            pdf_path = self.browser_download_path / f"建设银行_电子回单_{datetime.now().strftime('%Y%m%d')}.pdf"
            new_page.pdf(path=str(pdf_path))
            logger.info(f"已保存电子回单PDF: {pdf_path}")
            return self._move_to_download_path(pdf_path)
        except Exception as e:
            logger.error(f"新页面打印失败: {e}")
            raise
        finally:
            try:
                new_page.close()
            except Exception:
                pass
    
    def _get_downloaded_files_before(self):
        return get_downloaded_files_before(self.browser_download_path)

    def _wait_and_find_new_file(self, before_files, timeout: int = 60):
        return wait_and_find_new_file(before_files, timeout)

    def _move_to_download_path(self, source_file: Path) -> str:
        """将文件移动到归档目录，按指定格式重命名"""
        timestamp = datetime.now().strftime("%Y%m%d")
        ext = source_file.suffix or ".pdf"
        
        # 文件名格式: 网银名称_电子回单_yyyymmdd
        file_name = f"建设银行_电子回单_{timestamp}{ext}"
        # 使用归档路径（按年月分类）
        file_path = self.archive_path / file_name
        
        if file_path.exists():
            logger.info(f"文件已存在，将覆盖: {file_path}")
        
        shutil.move(str(source_file), str(file_path))
        logger.info(f"电子回单已保存: {file_path}")
        return str(file_path)
