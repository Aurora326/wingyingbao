# -*- coding: utf-8 -*-
"""
银行流水采集模块 - 宁波银行适配版
"""
import sys
import time
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from playwright.sync_api import Page
from logger import setup_logger
from common.download_helper import get_downloaded_files_before, wait_and_find_new_file
from config_loader import StatementsConfig

logger = setup_logger()


class StatementCollector:
    """银行流水采集器 - 宁波银行适配版"""
    
    def __init__(self, page: Page, config: StatementsConfig, browser_download_path: Optional[Path] = None):
        self.page = page
        self.config = config
        self.download_path = Path(config.download_path)
        self.download_path.mkdir(parents=True, exist_ok=True)
        
        # 调试：输出配置值
        logger.info(f"[DEBUG] StatementsConfig file_format: {getattr(config, 'file_format', 'NOT_SET')}")
        
        # 设置归档路径（按年月）
        self.current_date = datetime.now()
        if config.archive_by_month:
            self.archive_path = self.download_path / f"{self.current_date.year}" / f"{self.current_date.month:02d}"
            self.archive_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"流水归档路径: {self.archive_path}")
            # 清理旧数据
            self._cleanup_old_archives()
        else:
            self.archive_path = self.download_path
        
        # 浏览器下载路径（优先使用外部传入的路径，与 base_browser 保持一致）
        if browser_download_path:
            self.browser_download_path = Path(browser_download_path)
            logger.info(f"使用传入的浏览器下载路径: {self.browser_download_path}")
        else:
            # 支持打包环境：检测是否在 PyInstaller 环境中运行
            if getattr(sys, 'frozen', False):
                self.browser_download_path = Path(sys.executable).parent / "downloads"
                logger.info(f"[打包环境] 浏览器下载路径: {self.browser_download_path}")
            else:
                # 开发环境，使用脚本所在目录
                self.browser_download_path = Path(__file__).parent / "downloads"
                logger.info(f"[开发环境] 浏览器下载路径: {self.browser_download_path}")
        
        # 确保下载目录存在
        self.browser_download_path.mkdir(parents=True, exist_ok=True)
    
    def _cleanup_old_archives(self):
        """清理超过保留期限的归档目录"""
        retention_months = self.config.retention_months
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
        
        logger.info(f"清理 {retention_months} 个月前的流水归档数据，截止日期: {cutoff_date.strftime('%Y-%m')}")
        
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
    
    def collect(self, accounts: Optional[List[str]] = None) -> List[str]:
        """采集银行流水"""
        downloaded_files = []
        target_accounts = accounts or self.config.accounts
        
        if self.config.start_date and self.config.end_date:
            start_date_str = self.config.start_date
            end_date_str = self.config.end_date
        else:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=self.config.days_back)
            start_date_str = start_date.strftime("%Y-%m-%d")
            end_date_str = end_date.strftime("%Y-%m-%d")
        
        logger.info(f"开始采集流水，日期范围: {start_date_str} 至 {end_date_str}")
        
        try:
            # 导航到明细查询页面
            self._navigate_to_statement_page()
            
            if not target_accounts:
                target_accounts = self._get_all_accounts()
            
            for account in target_accounts:
                try:
                    file_path = self._download_statement(account, start_date_str, end_date_str)
                    if file_path:
                        downloaded_files.append(file_path)
                except Exception as e:
                    logger.error(f"采集账户 {account} 流水失败: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"采集流水过程出错: {e}")
        
        logger.info(f"流水采集完成，共下载 {len(downloaded_files)} 个文件")
        return downloaded_files
    
    def _navigate_to_statement_page(self):
        """导航到交易明细查询页面 - 宁波银行适配"""
        logger.info("导航到交易明细查询页面...")
        time.sleep(2)
        
        # 先处理可能的弹窗
        try:
            restore_btn = self.page.locator("button:has-text('恢复'), .restore-btn, [role='dialog'] button").first
            if restore_btn.count() > 0 and restore_btn.is_visible(timeout=2000):
                logger.info("检测到恢复页面弹窗，点击关闭")
                self.page.keyboard.press("Escape")
                time.sleep(0.5)
        except Exception:
            pass
        
        # 尝试点击页面空白处关闭可能展开的菜单
        try:
            self.page.mouse.click(100, 100)
            time.sleep(0.5)
        except Exception:
            pass
        
        # 方式1：直接通过 URL 导航（最快最可靠）
        try:
            logger.info("方式1: 直接通过 URL 导航到明细查询页面...")
            # 宁波银行明细查询页面 URL 格式
            base_url = "https://cb.nbcb.com.cn/microbase/microhome/index.html"
            detail_url = f"{base_url}#/entfinbank-account2-web/010102"
            self.page.goto(detail_url, wait_until="networkidle", timeout=30000)
            
            if self._is_statement_page():
                logger.info("✓ 已成功进入账户明细查询页面（通过直接URL）")
                return
            else:
                logger.warning("URL导航后未检测到明细页面，尝试其他方式...")
        except Exception as e:
            logger.warning(f"URL导航失败: {e}")
        
        # 方式2：尝试点击菜单导航 - 宁波银行菜单结构
        try:
            logger.info("方式2: 尝试通过菜单导航...")
            # 点击"账户查询"或"账户管理"菜单
            account_menu = self.page.locator('text=账户查询, text=账户管理, .account-menu, [data-menu="account"]').first
            if account_menu.count() > 0 and account_menu.is_visible(timeout=3000):
                account_menu.click()
                time.sleep(1)
                
                # 点击"交易明细查询"或"账户明细"
                detail_menu = self.page.locator('text=交易明细查询, text=账户明细, text=明细查询, .detail-query').first
                if detail_menu.count() > 0 and detail_menu.is_visible(timeout=3000):
                    detail_menu.click()
                    
                    try:
                        self.page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                    time.sleep(1)
                    
                    if self._is_statement_page():
                        logger.info("✓ 已成功进入账户明细查询页面（通过菜单）")
                        return
        except Exception as e:
            logger.debug(f"菜单导航失败: {e}")
        
        # 方式3：尝试执行JavaScript函数
        try:
            logger.info("方式3: 尝试执行 JavaScript 进入账户明细...")
            self.page.evaluate("""() => {
                // 尝试直接修改hash到明细查询页面
                window.location.hash = '#/entfinbank-account2-web/010102';
            }""")
            time.sleep(3)
            if self._is_statement_page():
                logger.info("✓ 已成功进入账户明细查询页面（通过JS）")
                return
        except Exception as e:
            logger.warning(f"JS调用失败: {e}")
        
        logger.warning("未能自动导航到交易明细页面，请手动导航")
        # 等待用户手动导航
        start = time.time()
        while time.time() - start < 60:
            if self._is_statement_page():
                logger.info("检测到已进入交易明细页面")
                return
            time.sleep(2)
        
        raise Exception("未能进入交易明细查询页面")
    
    def _is_statement_page(self) -> bool:
        """检查当前是否在交易明细查询页面 - 宁波银行适配"""
        # 检测所有 frame（包括iframe）
        contexts = [self.page] + list(self.page.frames)
        
        indicators = [
            "text=交易明细查询",
            "text=账户明细查询",
            "text=明细查询",
            'input[placeholder*="查询起始日期"]',  # 宁波银行开始日期
            'input[placeholder*="查询终止日期"]',  # 宁波银行结束日期
            '.eb-range-input',  # 宁波银行日期范围输入框
            '.eb-date-editor--daterange',  # 日期范围编辑器
            'input[name="startDate"]',
            'input[name="endDate"]',
            'input[name="beginDate"]',
            '#startDate',
            '#endDate',
            '.eb-button--default',  # 宁波银行按钮样式
            "button:has-text('查询')",
        ]
        
        for context in contexts:
            for indicator in indicators:
                try:
                    locator = context.locator(indicator).first
                    if locator.count() > 0 and locator.is_visible(timeout=500):
                        logger.info(f"检测到明细页面: {indicator}")
                        return True
                except Exception:
                    continue
        return False
    
    def _get_all_accounts(self) -> List[str]:
        """获取所有可用账户"""
        logger.info("正在获取账户列表...")
        accounts = []
        
        try:
            # 点击账户下拉框
            dropdown_selectors = [
                ".account-select",
                "select[name='account']",
                ".el-select",
                "text=请选择账户",
                "#accountSelect",
                ".dropdown-toggle",
            ]
            
            for selector in dropdown_selectors:
                try:
                    dropdown = self.page.locator(selector).first
                    if dropdown.is_visible(timeout=3000):
                        dropdown.click()
                        time.sleep(1)
                        
                        # 获取选项
                        options = self.page.locator(".el-select-dropdown__item, option, .dropdown-item, .account-option").all()
                        for option in options:
                            text = option.text_content()
                            if text and text.strip():
                                accounts.append(text.strip())
                        
                        if accounts:
                            break
                except Exception:
                    continue
                    
        except Exception as e:
            logger.debug(f"获取账户列表失败: {e}")
        
        if not accounts:
            logger.info("未获取到账户列表，将使用默认账户")
            return [""]
        
        logger.info(f"获取到 {len(accounts)} 个账户")
        return accounts
    
    def _create_no_data_marker(self, start_date: str, end_date: str) -> str:
        """
        创建无数据标记文件
        
        Args:
            start_date: 查询开始日期
            end_date: 查询结束日期
            
        Returns:
            标记文件路径
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"宁波银行_银行流水_无数据_{timestamp}.txt"
        # 使用归档路径（按年月分类）
        file_path = self.archive_path / file_name
        
        content = f"""宁波银行企业网银 - 银行流水采集结果
========================================
查询时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
查询日期范围: {start_date} 至 {end_date}
查询账户: {self.config.accounts if self.config.accounts else '默认账户'}

查询结果: 无数据
说明: 在指定的日期范围内未查询到任何交易记录。

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
            return "当前查询结果为空"
    
    def _check_has_data(self) -> bool:
        """检查查询结果是否有数据"""
        try:
            no_data_indicators = [
                "text=无数据显示",
                "text=暂无数据",
                "text=未查询到",
                "text=暂无符合条件的查询结果",
                ".no-data",
                ".empty",
                "text=没有记录",
            ]
            for indicator in no_data_indicators:
                try:
                    if self.page.locator(indicator).first.is_visible(timeout=2000):
                        return False
                except Exception:
                    continue
            return True
        except Exception:
            return True
    
    def _get_downloaded_files_before(self):
        return get_downloaded_files_before(self.browser_download_path)

    def _wait_and_find_new_file(self, before_files, timeout: int = 60):
        return wait_and_find_new_file(before_files, timeout)

    def _move_to_download_path(self, source_file: Path, account: str, end_date: str) -> str:
        """将文件移动到归档目录，按指定格式重命名"""
        timestamp = datetime.now().strftime("%Y%m%d")
        ext = source_file.suffix or ".xlsx"
        
        # 检查配置文件中的格式设置
        file_format = getattr(self.config, 'file_format', 'excel').lower()
        
        # 如果配置为 xls，修改文件后缀（直接重命名，不转换格式）
        if file_format == 'xls' and ext == '.xlsx':
            new_path = source_file.with_suffix('.xls')
            source_file.rename(new_path)
            source_file = new_path
            ext = '.xls'
            logger.info(f"已将文件后缀修改为 .xls: {source_file}")
        
        # 文件名格式: 网银名称_银行流水_yyyymmdd
        file_name = f"宁波银行_银行流水_{timestamp}{ext}"
        # 使用归档路径（按年月分类）
        file_path = self.archive_path / file_name
        
        if file_path.exists():
            logger.info(f"文件已存在，将覆盖: {file_path}")
        
        shutil.move(str(source_file), str(file_path))
        logger.info(f"流水文件已保存: {file_path}")
        return str(file_path)
    
    def _convert_xlsx_to_xls(self, xlsx_file: Path) -> Optional[Path]:
        """将 xlsx 文件转换为 xls 格式"""
        try:
            # 导入必要的库
            import pandas as pd
            import xlwt
            
            xls_file = xlsx_file.with_suffix('.xls')
            
            # 读取 xlsx - 使用 openpyxl 引擎
            df = pd.read_excel(xlsx_file, sheet_name=None, engine='openpyxl')
            
            # 写入 xls（使用 xlwt 引擎）
            with pd.ExcelWriter(xls_file, engine='xlwt') as writer:
                for sheet_name, sheet_df in df.items():
                    # xlwt 有行数限制（65535行），超过会报错，需要截断
                    if len(sheet_df) > 65535:
                        logger.warning(f"工作表 '{sheet_name}' 行数超过65535，已截断")
                        sheet_df = sheet_df.iloc[:65535]
                    sheet_df.to_excel(writer, sheet_name=sheet_name, index=False)
            
            # 删除原 xlsx 文件
            xlsx_file.unlink()
            logger.info(f"转换成功: {xls_file}")
            return xls_file
            
        except ImportError as e:
            logger.error(f"缺少必要的库: {e}")
            logger.error(r"请运行: .\venv\Scripts\pip install pandas xlwt openpyxl")
            return None
        except Exception as e:
            logger.error(f"转换失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def _set_date_range(self, start_date: str, end_date: str):
        """设置日期范围 - 宁波银行适配"""
        logger.info(f"设置日期范围: {start_date} 至 {end_date}")
        
        time.sleep(1.5)
        
        # 查找包含日期输入框的 frame
        target_frame = None
        for frame in self.page.frames:
            try:
                if frame.locator('input[placeholder*="开始日期"], input[placeholder*="结束日期"]').count() > 0:
                    target_frame = frame
                    break
            except Exception:
                continue
        
        page_or_frame = target_frame if target_frame else self.page
        
        try:
            # 设置开始日期和结束日期（宁波银行适配）
            self._set_date_field_by_playwright("startDate", start_date, page_or_frame)
            self._set_date_field_by_playwright("beginDate", start_date, page_or_frame)
            self._set_date_field_by_playwright("endDate", end_date, page_or_frame)
            
            # 关闭日历 - 使用页面 keyboard
            self.page.keyboard.press("Escape")
            time.sleep(0.2)
            self.page.keyboard.press("Escape")
            time.sleep(0.2)
            
            # 点击"至"标签或空白处关闭日历
            try:
                page_or_frame.locator("text=至, .date-separator").first.click()
                time.sleep(0.2)
            except Exception:
                pass
            
            # 验证设置（宁波银行适配）
            try:
                begin_value = page_or_frame.locator('input[placeholder*="查询起始日期"]').first.input_value()
                if begin_value:
                    logger.info(f"当前开始日期值: {begin_value}")
            except Exception:
                pass
            
            try:
                end_value = page_or_frame.locator('input[placeholder*="查询终止日期"]').first.input_value()
                if end_value:
                    logger.info(f"当前结束日期值: {end_value}")
            except Exception:
                pass
            
        except Exception as e:
            logger.warning(f"设置日期范围失败: {e}")
    
    def _set_date_field_by_selector(self, selector: str, date_value: str):
        """使用选择器设置日期字段"""
        try:
            input_field = self.page.locator(selector).first
            input_field.click()
            time.sleep(0.5)
            input_field.fill(date_value)
            time.sleep(0.5)
            input_field.press("Escape")  # 关闭可能的日期选择器
            logger.info(f"已设置日期: {date_value}")
        except Exception as e:
            logger.warning(f"通过选择器设置日期失败: {e}")
    
    def _set_date_field(self, field_name: str, date_value: str, page_or_frame=None):
        """使用JavaScript设置日期字段 - 宁波银行专用"""
        try:
            context = page_or_frame or self.page
            result = context.evaluate("""
                (function(args) {
                    var input = document.getElementById(args.field_name);
                    if (input) {
                        input.removeAttribute('readonly');
                        input.removeAttribute('disabled');
                        input.value = args.date_value;
                        ['focus', 'input', 'change'].forEach(function(eventType) {
                            var event = new Event(eventType, { bubbles: true });
                            input.dispatchEvent(event);
                        });
                        return { success: true, value: input.value };
                    }
                    return { success: false, error: 'Element not found' };
                })()
            """, {"field_name": field_name, "date_value": date_value})
            logger.info(f"日期设置结果: {result}")
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"使用JavaScript设置日期失败: {e}")
    
    def _set_date_field_by_playwright(self, field_id: str, date_value: str, page_or_frame=None):
        """使用 Playwright 直接设置日期 - 宁波银行适配"""
        try:
            context = page_or_frame or self.page
            
            # 宁波银行日期格式转换 (YYYY-MM-DD)
            formatted_date = date_value.replace('/', '-')
            
            # 根据 field_id 映射到实际的选择器（宁波银行适配）
            placeholder_map = {
                'startDate': '查询起始日期',
                'beginDate': '查询起始日期',
                'endDate': '查询终止日期',
            }
            placeholder = placeholder_map.get(field_id, field_id)
            
            # 尝试多种选择器（宁波银行适配）
            selectors = [
                f'input[placeholder*="{placeholder}"]',  # placeholder 匹配
                f'.eb-range-input[placeholder*="{placeholder}"]',  # 日期范围输入框
                f'input[name="{field_id}"]',
                f'#{field_id}',
                f'.eb-input__inner[placeholder*="{placeholder}"]',  # 宁波银行 input 样式
            ]
            
            input_field = None
            used_selector = None
            for selector in selectors:
                try:
                    elem = context.locator(selector).first
                    if elem.count() > 0 and elem.is_visible(timeout=2000):
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
            
            # 先点击激活输入框
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
            
            # 验证设置是否成功
            actual_value = input_field.input_value()
            logger.info(f"日期设置结果: {field_id} = {actual_value}")
            return True
            
        except Exception as e:
            logger.error(f"设置 {field_id} 失败: {e}")
            return False
    
    def _set_date_field_by_js_direct(self, field_id: str, date_value: str, page_or_frame=None):
        """直接使用JS设置日期 - 针对宁波银行"""
        try:
            context = page_or_frame or self.page
            # 使用 JS 设置值
            result = context.evaluate("""
                (function(args) {
                    var input = document.getElementById(args.field_id);
                    if (input) {
                        input.removeAttribute('readonly');
                        input.removeAttribute('disabled');
                        input.value = args.date_value;
                        ['focus', 'click', 'input', 'change', 'blur'].forEach(function(eventType) {
                            var event = new Event(eventType, { bubbles: true });
                            input.dispatchEvent(event);
                        });
                        return { success: true, value: input.value };
                    }
                    return { success: false };
                })()
            """, {"field_id": field_id, "date_value": date_value})
            logger.info(f"JS设置 {field_id} 结果: {result}")
            time.sleep(0.3)
        except Exception as e:
            logger.warning(f"JS 设置 {field_id} 失败: {e}")
    
    def _download_statement(self, account: str, start_date: str, end_date: str) -> Optional[str]:
        """下载单个账户的流水"""
        logger.info(f"正在处理账户 {account} 的流水...")
        
        # 如果指定了账号，尝试选择账号（宁波银行账号通常已默认选中）
        if account and account.strip():
            try:
                # 检查当前选中的账号是否匹配
                account_select = self.page.locator("#acctNo, select[name='account'], .account-select").first
                if account_select.count() > 0:
                    current_value = account_select.input_value()
                    logger.info(f"当前选中账号: {current_value}")
                    # 如果当前账号不匹配，尝试切换
                    if account not in current_value:
                        logger.info(f"尝试切换账号到: {account}")
                        # 宁波银行通常不需要切换，账号已在首页确定
            except Exception as e:
                logger.debug(f"检查账号失败: {e}")
        
        # 设置日期范围
        self._set_date_range(start_date, end_date)
        
        # 再次确认日期值（宁波银行适配）
        try:
            begin_val = self.page.locator('input[placeholder*="查询起始日期"]').first.input_value()
            end_val = self.page.locator('input[placeholder*="查询终止日期"]').first.input_value()
            if begin_val and end_val:
                logger.info(f"查询前日期确认: {begin_val} 至 {end_val}")
        except Exception:
            pass
        
        # 点击查询按钮
        logger.info("点击查询按钮...")
        try:
            # 检测是否在 iframe 中
            query_context = self.page
            for frame in self.page.frames:
                try:
                    if frame.locator("#queryButton").count() > 0:
                        query_context = frame
                        logger.info("在 iframe 中找到查询按钮")
                        break
                except Exception:
                    continue
            
            query_selectors = [
                'button.eb-button--default:has-text("查询")',  # 宁波银行查询按钮
                'button:has(.eb-icon-search)',  # 有搜索图标的按钮
                "button:has-text('查询')",
                "#queryButton",
                "input[type='submit']",
                "a:has-text('查询')",
                "#queryBtn",
                ".query-btn",
                "button[type='submit']",
            ]
            
            btn_clicked = False
            for selector in query_selectors:
                try:
                    btn = query_context.locator(selector).first
                    if btn.count() > 0 and btn.is_visible(timeout=3000):
                        # 滚动到视野中
                        btn.scroll_into_view_if_needed()
                        time.sleep(0.3)
                        # 点击
                        btn.click()
                        logger.info(f"已点击查询按钮: {selector}")
                        btn_clicked = True
                        try:
                            self.page.wait_for_load_state("networkidle", timeout=10000)
                        except Exception:
                            pass
                        time.sleep(1)
                        break
                except Exception:
                    continue
            
            if not btn_clicked:
                logger.warning("未找到查询按钮")
        except Exception as e:
            logger.error(f"点击查询按钮失败: {e}")
            return None
        
        # 检查是否有数据
        if not self._check_has_data():
            logger.info("当前查询结果为空")
            return self._create_no_data_marker(start_date, end_date)
        
        logger.info("检测到数据，开始下载...")
        
        # 点击下载按钮
        try:
            # 记录下载前的文件列表
            before_files = self._get_downloaded_files_before()
            
            # 检测是否在 iframe 中查找下载按钮
            download_context = self.page
            for frame in self.page.frames:
                try:
                    if frame.locator("#saveButtonXLS, #saveButtonPDF").count() > 0:
                        download_context = frame
                        logger.info("在 iframe 中找到下载按钮")
                        break
                except Exception:
                    continue
            
            download_selectors = [
                'button:has-text("下载全部明细")',  # 宁波银行特有按钮
                'button:has-text("导出TXT")',      # 宁波银行导出按钮
                "button:has-text('下载')",
                "button:has-text('导出')",
                'a:has-text("下载全部明细")',
                'a:has-text("导出TXT")',
                "a:has-text('下载')",
                "a:has-text('导出')",
                "#saveButtonXLS",
                "#saveButtonPDF",
                "#downloadBtn",
                ".download-btn",
                ".export-btn",
            ]
            
            download_btn = None
            for selector in download_selectors:
                try:
                    btn = download_context.locator(selector).first
                    if btn.count() > 0 and btn.is_visible(timeout=3000):
                        download_btn = btn
                        logger.info(f"找到下载按钮: {selector}")
                        break
                except Exception:
                    continue
            
            if download_btn:
                # 滚动到视野并点击
                download_btn.scroll_into_view_if_needed()
                time.sleep(0.5)
                
                before_files = self._get_downloaded_files_before()
                try:
                    with self.page.expect_download(timeout=60000) as download_info:
                        download_btn.click()
                    download = download_info.value
                    temp_path = self.browser_download_path / download.suggested_filename
                    download.save_as(str(temp_path))
                    logger.info(f"Playwright 下载完成: {temp_path}")
                    
                    if temp_path.suffix in ['.html', '.htm']:
                        logger.warning(f"下载的是HTML文件，可能还未生成完成")
                        temp_path.unlink()
                        time.sleep(3)
                        return self._download_statement(account, start_date, end_date)
                    return self._move_to_download_path(temp_path, account, end_date)
                except Exception as e:
                    logger.warning(f"Playwright download监听失败，使用文件轮询备选: {e}")
                    try:
                        self.page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                    time.sleep(2)
                    
                    new_file = self._wait_and_find_new_file(before_files, timeout=60)
                    if new_file:
                        if new_file.suffix in ['.html', '.htm']:
                            logger.warning(f"下载的是HTML文件，可能还未生成完成")
                            new_file.unlink()
                            time.sleep(3)
                            return self._download_statement(account, start_date, end_date)
                        return self._move_to_download_path(new_file, account, end_date)
                    
                    logger.warning("下载失败：未找到下载文件")
            else:
                logger.warning("未找到下载按钮")
            
            return None
                    
        except Exception as e:
            logger.error(f"下载过程出错: {e}")
            return None
