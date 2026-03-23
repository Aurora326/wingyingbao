# -*- coding: utf-8 -*-
"""
银行流水采集模块 - 中信银行适配版
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
    """银行流水采集器 - 中信银行适配版"""
    
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
        
    def _close_survey_dialog(self):
        """关闭满意度调查弹窗"""
        try:
            # 检查是否有满意度调查页面
            survey_indicators = [
                "text=满意度调查",
                "text=中信银行企业网上银行2025年度满意度调查",
            ]
            
            has_survey = False
            for indicator in survey_indicators:
                try:
                    if self.page.locator(indicator).first.is_visible(timeout=2000):
                        has_survey = True
                        break
                except:
                    continue
            
            if not has_survey:
                return
            
            logger.info("检测到满意度调查页面，尝试关闭...")
            
            # 尝试点击第二个选项（"是，同时使用多家银行的企业网银"）
            try:
                option = self.page.locator("text=是，同时使用多家银行的企业网银").first
                if option.count() > 0 and option.is_visible(timeout=2000):
                    option.click()
                    logger.info("已选择满意度调查选项")
                    time.sleep(1)
            except Exception as e:
                logger.debug(f"点击选项失败: {e}")
            
            # 尝试找到并点击关闭/提交按钮
            close_selectors = [
                "button:has-text('提交')",
                "button:has-text('关闭')",
                "button:has-text('确定')",
                ".survey-close",
                ".close-btn",
            ]
            
            for selector in close_selectors:
                try:
                    btn = self.page.locator(selector).first
                    if btn.count() > 0 and btn.is_visible(timeout=2000):
                        btn.click()
                        logger.info(f"已点击调查页面按钮: {selector}")
                        time.sleep(2)
                        return
                except:
                    continue
                    
        except Exception as e:
            logger.debug(f"关闭满意度调查弹窗失败: {e}")
    
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
            # 先关闭满意度调查弹窗（如果有）
            self._close_survey_dialog()
            
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
        
        # 直接通过URL导航到流水查询页面
        statement_url = "https://corp.bank.ecitic.com/cotb/index_v5.html#/00102011"
        try:
            logger.info(f"直接访问流水查询URL: {statement_url}")
            self.page.goto(statement_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(5)  # 等待页面加载完成
            
            if self._is_statement_page():
                logger.info("已成功进入交易明细查询页面")
                # 关闭可能弹出的侧边栏菜单
                self._close_sidebar_menu()
                return
        except Exception as e:
            logger.error(f"URL导航失败: {e}")
        
        logger.warning("未能自动导航到交易明细页面，请手动导航")
        # 等待用户手动导航
        start = time.time()
        while time.time() - start < 60:
            if self._is_statement_page():
                logger.info("检测到已进入交易明细页面")
                return
            time.sleep(2)
        
        raise Exception("未能进入交易明细查询页面")
    
    def _close_sidebar_menu(self):
        """关闭侧边栏菜单弹窗"""
        try:
            # 点击页面空白区域或按 Escape 关闭侧边栏
            self.page.keyboard.press("Escape")
            time.sleep(0.5)
            # 尝试点击页面中间区域
            self.page.mouse.click(600, 400)
            time.sleep(0.5)
            logger.info("已关闭侧边栏菜单")
        except Exception as e:
            logger.debug(f"关闭侧边栏菜单失败: {e}")
    
    def _is_statement_page(self) -> bool:
        """检查当前是否在交易明细查询页面"""
        indicators = [
            "text=账户明细查询",
            "text=明细查询",
            "input[placeholder='起始日期']",
            "input[placeholder='截止日期']",
            "button:has-text('查询')",
            "text=选择日期",
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
            ]
            
            for selector in dropdown_selectors:
                try:
                    dropdown = self.page.locator(selector).first
                    if dropdown.is_visible(timeout=3000):
                        dropdown.click()
                        time.sleep(1)
                        
                        # 获取选项
                        options = self.page.locator(".el-select-dropdown__item, option, .dropdown-item").all()
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
        file_name = f"中信银行_银行流水_无数据_{timestamp}.txt"
        # 使用归档路径（按年月分类）
        file_path = self.archive_path / file_name
        
        content = f"""中信银行企业网银 - 银行流水采集结果
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
                        # 1. 已知扩展名的文件（.xlsx, .pdf等）
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
        
        # 如果超时，列出所有目录文件以便调试
        logger.warning("下载超时，检查各目录文件:")
        logger.warning(f"  browser_download_path: {self.browser_download_path}")
        logger.warning(f"  系统下载目录: {Path.home() / 'Downloads'}")
        for download_dir in download_dirs:
            if download_dir.exists():
                logger.warning(f"  目录: {download_dir}")
                file_count = 0
                for f in download_dir.iterdir():
                    if f.is_file():
                        file_count += 1
                        try:
                            size = f.stat().st_size
                            logger.warning(f"    {f.name}: {size} bytes")
                        except:
                            logger.warning(f"    {f.name}: 无法获取大小")
                if file_count == 0:
                    logger.warning(f"    (空目录)")
            else:
                logger.warning(f"  目录不存在: {download_dir}")
        
        return None
    
    def _move_to_download_path(self, source_file: Path, account: str, end_date: str) -> str:
        """将文件移动到归档目录，按指定格式重命名"""
        timestamp = datetime.now().strftime("%Y%m%d")
        ext = source_file.suffix or ".xlsx"
        
        # 文件名格式: 网银名称_银行流水_yyyymmdd
        file_name = f"中信银行_银行流水_{timestamp}{ext}"
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
            # 点击"其他"单选按钮
            other_radio = self.page.locator("text=其他").first
            if other_radio.count() > 0 and other_radio.is_visible(timeout=2000):
                other_radio.click()
                time.sleep(1)
            
            # 设置起始日期和结束日期
            self._set_date_field("startDate", start_date)
            self._set_date_field("endDate", end_date)
            
        except Exception as e:
            logger.warning(f"设置日期范围失败: {e}")
    
    def _set_date_field(self, field_name: str, date_value: str):
        """使用JavaScript设置日期字段"""
        try:
            self.page.evaluate(f"""
                var input = document.querySelector('input[name="{field_name}"]');
                if (input) {{
                    input.removeAttribute('readonly');
                    input.value = '{date_value}';
                    input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
            """)
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"设置日期失败: {e}")
    
    def _download_statement(self, account: str, start_date: str, end_date: str) -> Optional[str]:
        """下载单个账户的流水"""
        logger.info(f"正在处理账户 {account} 的流水...")
        
        # 设置日期范围
        self._set_date_range(start_date, end_date)
        
        # 点击查询按钮
        logger.info("点击查询按钮...")
        try:
            query_selectors = [
                "#m00102011 > div.panel-body > div > div:nth-child(3) > form > ul > li:nth-child(1) > button",
                "button[ng-click='qry()']",
                "button.ec-btn-red",
                "button:has-text('查询')",
                "#queryBtn",
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
        
        # 点击下载按钮 - 中信银行：先点击"全部下载"，再点击"Excel下载"
        try:
            # 记录下载前的文件列表
            before_files = self._get_downloaded_files_before()
            
            # 1. 先点击"全部下载"按钮
            all_download_selectors = [
                "#droparea-download-all-toggle",
                "button:has-text('全部下载')",
                "a:has-text('全部下载')",
                "text=全部下载",
            ]
            
            all_download_btn = None
            for selector in all_download_selectors:
                try:
                    btn = self.page.locator(selector).first
                    if btn.count() > 0 and btn.is_visible(timeout=2000):
                        all_download_btn = btn
                        logger.info(f"找到'全部下载'按钮: {selector}")
                        break
                except:
                    continue
            
            if all_download_btn:
                all_download_btn.click()
                logger.info("已点击'全部下载'按钮")
                time.sleep(2)
                
                # 2. 点击"Excel下载"选项
                excel_download_selectors = [
                    "#droparea-download-exc-download",
                    "text=Excel下载",
                    "a:has-text('Excel')",
                ]
                
                excel_download_btn = None
                for selector in excel_download_selectors:
                    try:
                        btn = self.page.locator(selector).first
                        if btn.count() > 0 and btn.is_visible(timeout=2000):
                            excel_download_btn = btn
                            logger.info(f"找到'Excel下载'选项: {selector}")
                            break
                    except:
                        continue
                
                if excel_download_btn:
                    # 直接使用JavaScript点击，避免弹窗阻塞
                    try:
                        excel_download_btn.evaluate("el => el.click()")
                        logger.info("已点击'Excel下载'选项（使用JavaScript）")
                    except:
                        excel_download_btn.click()
                        logger.info("已点击'Excel下载'选项")
                    
                    # 等待文件下载完成
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
                    logger.warning("未找到'Excel下载'选项")
            else:
                logger.warning("未找到'全部下载'按钮，尝试直接查找下载按钮...")
                # 尝试直接点击下载按钮
                download_selectors = [
                    "button:has-text('下载')",
                    "a:has-text('下载')",
                    "#downloadBtn",
                    ".download-btn",
                ]
                
                for selector in download_selectors:
                    try:
                        btn = self.page.locator(selector).first
                        if btn.count() > 0 and btn.is_visible(timeout=3000):
                            btn.click()
                            logger.info(f"点击下载按钮: {selector}")
                            time.sleep(3)
                            
                            new_file = self._wait_and_find_new_file(before_files, timeout=30)
                            if new_file:
                                return self._move_to_download_path(new_file, account, end_date)
                            break
                    except:
                        continue
            
            logger.warning("未找到下载按钮")
            return None
                    
        except Exception as e:
            logger.error(f"下载过程出错: {e}")
            return None
