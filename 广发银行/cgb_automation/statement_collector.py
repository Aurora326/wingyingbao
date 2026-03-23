# -*- coding: utf-8 -*-
"""
银行流水采集模块 - 广发银行适配版
"""
import sys
import time
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from playwright.sync_api import Page
from logger import setup_logger
from config_loader import StatementsConfig

logger = setup_logger()


class StatementCollector:
    """银行流水采集器 - 广发银行适配版"""
    
    def __init__(self, page: Page, config: StatementsConfig):
        self.page = page
        self.config = config
        self.download_path = Path(config.download_path)
        self.download_path.mkdir(parents=True, exist_ok=True)
        
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
        
        # 支持打包环境：检测是否在 PyInstaller 环境中运行
        if getattr(sys, 'frozen', False):
            # 打包后的 exe 环境，使用 exe 所在目录
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
        """导航到交易明细查询页面"""
        logger.info("导航到交易明细查询页面...")
        time.sleep(2)
        
        # 广发银行：尝试通过菜单导航到流水查询页面
        # 先尝试点击"账户查询"或"交易明细"菜单
        menu_selectors = [
            "text=账户查询",
            "text=交易明细",
            "text=明细查询",
            "a:has-text('账户查询')",
            "a:has-text('交易明细')",
            ".menu:has-text('账户查询')",
            ".menu:has-text('交易明细')",
        ]
        
        for selector in menu_selectors:
            try:
                menu = self.page.locator(selector).first
                if menu.count() > 0 and menu.is_visible(timeout=3000):
                    menu.click()
                    logger.info(f"已点击菜单: {selector}")
                    time.sleep(3)
                    
                    if self._is_statement_page():
                        logger.info("已成功进入交易明细查询页面")
                        return
            except:
                continue
        
        # 如果菜单点击失败，尝试直接访问URL
        # 广发银行流水查询页面URL（需要根据实际页面调整）
        statement_urls = [
            "https://ebank.cgbchina.com.cn/corporbank/account/queryDetail.do",
            "https://ebank.cgbchina.com.cn/corporbank/query/transDetail.do",
        ]
        
        for url in statement_urls:
            try:
                logger.info(f"尝试直接访问流水查询URL: {url}")
                self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(5)
                
                if self._is_statement_page():
                    logger.info("已成功进入交易明细查询页面")
                    return
            except Exception as e:
                logger.warning(f"URL导航失败: {e}")
        
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
        """检查当前是否在交易明细查询页面"""
        indicators = [
            "text=账户明细",
            "text=交易明细",
            "text=明细查询",
            "input[placeholder*='起始日期']",
            "input[placeholder*='开始日期']",
            "input[placeholder*='截止日期']",
            "input[placeholder*='结束日期']",
            "button:has-text('查询')",
            "text=选择日期",
            "text=交易日期",
        ]
        for indicator in indicators:
            try:
                if self.page.locator(indicator).first.is_visible(timeout=2000):
                    return True
            except:
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
                except:
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
        file_name = f"广发银行_银行流水_无数据_{timestamp}.txt"
        # 使用归档路径（按年月分类）
        file_path = self.archive_path / file_name
        
        content = f"""广发银行企业网银 - 银行流水采集结果
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
                except:
                    continue
            return True
        except:
            return True
    
    def _get_downloaded_files_before(self) -> dict:
        """获取多个下载目录中的文件列表（包括程序目录和系统下载目录）"""
        from pathlib import Path
        
        paths_to_check = [
            self.browser_download_path,
            Path.home() / "Downloads",
        ]
        
        all_files = {}
        for path in paths_to_check:
            if path.exists():
                files = set(f.name for f in path.iterdir() if f.is_file())
                all_files[path] = files
                logger.info(f"  目录 {path}: {len(files)} 个文件")
            else:
                all_files[path] = set()
                logger.info(f"  目录 {path}: 不存在")
        
        return all_files
    
    def _is_guid_filename(self, filename: str) -> bool:
        """检查文件名是否是GUID格式（Chrome临时下载文件）"""
        import re
        # 匹配GUID格式: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
        guid_pattern = r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
        return bool(re.match(guid_pattern, filename))
    
    def _wait_and_find_new_file(self, before_files: dict, timeout: int = 60) -> Optional[Path]:
        """等待并查找新下载的文件（支持多目录监控）"""
        start_time = time.time()
        
        # 合并所有目录的旧文件列表
        all_before_files = set()
        for path, files in before_files.items():
            all_before_files.update(files)
        
        logger.info(f"等待下载完成，初始监控文件数: {len(all_before_files)}")
        
        # 要监控的目录列表
        download_dirs = [
            self.browser_download_path,
            Path.home() / "Downloads",
        ]
        
        while time.time() - start_time < timeout:
            time.sleep(1)
            
            # 遍历所有下载目录
            for download_dir in download_dirs:
                if not download_dir.exists():
                    continue
                
                # 遍历当前目录下的所有文件
                for file_path in download_dir.iterdir():
                    if not file_path.is_file():
                        continue
                    
                    file_name = file_path.name
                    
                    # 检查是否是新文件（不在之前的文件列表中）
                    if file_name not in all_before_files:
                        # 获取文件大小
                        try:
                            file_size = file_path.stat().st_size
                        except:
                            file_size = 0
                        
                        logger.info(f"发现新文件: {file_name} (大小: {file_size} bytes) 在目录: {download_dir}")
                        
                        # 判断是否是有效文件：
                        # 1. 已知扩展名的文件（.xlsx, .xls, .pdf, .csv等）
                        # 2. 大于100字节的文件
                        # 3. GUID格式的临时文件（Chrome下载临时文件）且大小合理
                        is_valid = (
                            file_name.endswith(('.xlsx', '.xls', '.pdf', '.csv')) or
                            file_size > 1000 or
                            (self._is_guid_filename(file_name) and file_size > 100)
                        )
                        
                        if is_valid:
                            logger.info(f"确认新下载文件: {file_path}")
                            # 等待文件稳定
                            stable_count = 0
                            last_size = -1
                            while stable_count < 3:
                                time.sleep(0.5)
                                if file_path.exists():
                                    current_size = file_path.stat().st_size
                                    if current_size == last_size:
                                        stable_count += 1
                                    else:
                                        stable_count = 0
                                        last_size = current_size
                                else:
                                    break
                            return file_path
        
        # 如果超时，查找各目录中最新的文件作为备选
        logger.warning("下载超时，尝试查找最新文件...")
        newest = None
        newest_mtime = 0
        
        for download_dir in download_dirs:
            if not download_dir.exists():
                continue
            for f in download_dir.iterdir():
                if not f.is_file():
                    continue
                try:
                    mtime = f.stat().st_mtime
                    # 文件应该比开始时间新，且比已知的旧文件都新
                    if mtime > start_time and mtime > newest_mtime:
                        newest = f
                        newest_mtime = mtime
                except:
                    continue
        
        if newest:
            logger.info(f"找到最新文件作为备选: {newest}")
        return newest
    
    def _move_to_download_path(self, source_file: Path, account: str, end_date: str) -> str:
        """将文件移动到归档目录，按指定格式重命名"""
        timestamp = datetime.now().strftime("%Y%m%d")
        ext = source_file.suffix or ".xlsx"
        
        # 文件名格式: 网银名称_银行流水_yyyymmdd
        file_name = f"广发银行_银行流水_{timestamp}{ext}"
        # 使用归档路径（按年月分类）
        file_path = self.archive_path / file_name
        
        if file_path.exists():
            logger.info(f"文件已存在，将覆盖: {file_path}")
        
        shutil.move(str(source_file), str(file_path))
        logger.info(f"流水文件已保存: {file_path}")
        return str(file_path)
    
    def _set_date_range(self, start_date: str, end_date: str):
        """设置日期范围"""
        logger.info(f"设置日期范围: {start_date} 至 {end_date}")
        
        try:
            # 广发银行可能的日期输入方式
            # 1. 直接设置日期输入框
            start_selectors = [
                "input[placeholder*='起始']",
                "input[placeholder*='开始']",
                "input[name='startDate']",
                "input[id='startDate']",
                "input[name='beginDate']",
            ]
            
            end_selectors = [
                "input[placeholder*='截止']",
                "input[placeholder*='结束']",
                "input[name='endDate']",
                "input[id='endDate']",
            ]
            
            # 尝试设置开始日期
            for selector in start_selectors:
                try:
                    start_input = self.page.locator(selector).first
                    if start_input.count() > 0 and start_input.is_visible(timeout=2000):
                        self._set_date_field_by_selector(selector, start_date)
                        break
                except:
                    continue
            
            # 尝试设置结束日期
            for selector in end_selectors:
                try:
                    end_input = self.page.locator(selector).first
                    if end_input.count() > 0 and end_input.is_visible(timeout=2000):
                        self._set_date_field_by_selector(selector, end_date)
                        break
                except:
                    continue
            
            # 如果使用JavaScript设置失败，尝试使用Playwright直接操作
            self._set_date_field("startDate", start_date)
            self._set_date_field("endDate", end_date)
            
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
    
    def _set_date_field(self, field_name: str, date_value: str):
        """使用JavaScript设置日期字段"""
        try:
            self.page.evaluate(f"""
                var input = document.querySelector('input[name="{field_name}"]') || 
                           document.querySelector('input[id="{field_name}"]');
                if (input) {{
                    input.removeAttribute('readonly');
                    input.value = '{date_value}';
                    input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
            """)
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"使用JavaScript设置日期失败: {e}")
    
    def _download_statement(self, account: str, start_date: str, end_date: str) -> Optional[str]:
        """下载单个账户的流水"""
        logger.info(f"正在处理账户 {account} 的流水...")
        
        # 设置日期范围
        self._set_date_range(start_date, end_date)
        
        # 点击查询按钮
        logger.info("点击查询按钮...")
        try:
            query_selectors = [
                "button:has-text('查询')",
                "input[type='submit']",
                "a:has-text('查询')",
                "#queryBtn",
                ".query-btn",
                "button[type='submit']",
            ]
            
            btn_clicked = False
            for selector in query_selectors:
                try:
                    btn = self.page.locator(selector).first
                    if btn.count() > 0 and btn.is_visible(timeout=2000):
                        btn.click()
                        logger.info(f"已点击查询按钮: {selector}")
                        btn_clicked = True
                        time.sleep(3)
                        break
                except:
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
            
            download_selectors = [
                "button:has-text('下载')",
                "a:has-text('下载')",
                "button:has-text('导出')",
                "a:has-text('导出')",
                "#downloadBtn",
                ".download-btn",
                "text=Excel下载",
                "text=导出Excel",
            ]
            
            download_btn = None
            for selector in download_selectors:
                try:
                    btn = self.page.locator(selector).first
                    if btn.count() > 0 and btn.is_visible(timeout=3000):
                        download_btn = btn
                        logger.info(f"找到下载按钮: {selector}")
                        break
                except:
                    continue
            
            if download_btn:
                # 直接使用JavaScript点击，避免弹窗阻塞
                try:
                    download_btn.evaluate("el => el.click()")
                    logger.info("已点击下载按钮（使用JavaScript）")
                except:
                    download_btn.click()
                    logger.info("已点击下载按钮")
                
                time.sleep(3)
                
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
