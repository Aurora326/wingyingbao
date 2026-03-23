# -*- coding: utf-8 -*-
"""
电子回单采集模块 - 中信银行适配版
"""
import time
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict
from playwright.sync_api import Page
from logger import setup_logger

logger = setup_logger()


class ReceiptCollector:
    """电子回单采集器 - 中信银行适配版"""
    
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
                marker_file = self._create_no_data_marker()
                return [marker_file] if marker_file else []
            
            # 全选回单
            self._select_all_receipts()
            
            # 点击打印/下载并下载文件
            file_path = self._click_print_download()
            
            # 如果上面的下载没成功，尝试备选方案
            if not file_path:
                file_path = self._download_receipts()
            
            if file_path:
                downloaded_files.append(file_path)
                    
        except Exception as e:
            logger.error(f"采集电子回单过程出错: {e}")
        
        logger.info(f"电子回单采集完成，共下载 {len(downloaded_files)} 个文件")
        return downloaded_files
    
    def _navigate_to_receipt_page(self) -> bool:
        """导航到电子回单查询页面"""
        logger.info("导航到电子回单查询页面...")
        
        try:
            # 使用直接URL导航
            receipt_url = "https://corp.bank.ecitic.com/cotb/index_v5.html#/00103011"
            try:
                self.page.goto(receipt_url, wait_until="domcontentloaded", timeout=30000)
            except:
                pass
            logger.info(f"直接访问电子回单URL: {receipt_url}")
            time.sleep(5)
            
            if self._is_receipt_page():
                logger.info("已成功进入电子回单查询页面")
                self._close_sidebar_menu()
                return True
            
            logger.error("URL导航失败")
            return False
            
        except Exception as e:
            logger.error(f"导航失败: {e}")
            return False
    
    def _is_receipt_page(self) -> bool:
        """检查当前是否在电子回单页面"""
        indicators = [
            "text=电子回单",
            "text=回单查询",
            "input[name='startDate']",
            ".receipt-table",
            "button:has-text('打印')",
            "button:has-text('下载')",
        ]
        for indicator in indicators:
            try:
                if self.page.locator(indicator).first.is_visible(timeout=2000):
                    return True
            except:
                continue
        return False
    
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
            logger.warning(f"关闭侧边栏菜单失败: {e}")
    
    def _set_query_conditions(self):
        """设置查询日期条件"""
        logger.info(f"设置查询日期范围: {self.start_date} 至 {self.end_date}")
        
        try:
            time.sleep(2)
            
            # 点击"其他"单选按钮
            other_radio = self.page.locator("text=其他").first
            if other_radio.count() > 0 and other_radio.is_visible(timeout=2000):
                other_radio.click()
                time.sleep(1)
            
            # 设置起始日期
            self._set_date_field("startDate", self.start_date)
            # 设置结束日期
            self._set_date_field("endDate", self.end_date)
                
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
            logger.info(f"已设置日期: {date_value}")
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"设置日期失败: {e}")
    
    def _click_query(self) -> bool:
        """点击查询按钮"""
        logger.info("点击查询按钮...")
        
        try:
            btn = self.page.locator("#m00103011 > div.panel-body > div > div.form-group.col-xs-12.clearfix > div > div > div > ul > li:nth-child(1) > button").first
            if btn.count() > 0 and btn.is_visible(timeout=2000):
                btn.click()
                time.sleep(3)
                return True
            
            logger.warning("未找到查询按钮")
            return False
            
        except Exception as e:
            logger.error(f"点击查询按钮失败: {e}")
            return False
    
    def _create_no_data_marker(self) -> str:
        """
        创建无数据标记文件
        
        Returns:
            标记文件路径
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"中信银行_电子回单_无数据_{timestamp}.txt"
        # 使用归档路径（按年月分类）
        file_path = self.archive_path / file_name
        
        content = f"""中信银行企业网银 - 电子回单采集结果
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
                "#nothing",           # 回单无数据的特定ID
                ".no-data",
                ".empty",
            ]
            for indicator in no_data_indicators:
                try:
                    if self.page.locator(indicator).first.is_visible(timeout=2000):
                        logger.info("查询结果为空")
                        return False
                except:
                    continue
            
            # 检查表格行
            rows = self.page.locator("table tbody tr, .receipt-row, .data-row").all()
            if len(rows) > 0:
                logger.info(f"查询到 {len(rows)} 条记录")
                return True
            
            return True
        except:
            return True
    
    def _select_all_receipts(self):
        """全选回单"""
        logger.info("全选回单...")
        
        try:
            checkbox = self.page.locator("th input[type='checkbox']").first
            if checkbox.count() > 0 and checkbox.is_visible(timeout=2000):
                checkbox.click()
                time.sleep(1)
            else:
                logger.warning("未找到全选框")
        except Exception as e:
            logger.warning(f"全选回单失败: {e}")
    
    def _click_print_download(self) -> Optional[str]:
        """点击打印/下载按钮触发下载对话框 - 中信银行：先点击全部下载，再点击PDF，并监听下载"""
        logger.info("点击打印/下载按钮...")
        
        # 记录下载前的文件列表
        before_files = self._get_downloaded_files_before()
        
        try:
            # 1. 先点击"全部下载"按钮
            all_download_btn = self.page.locator("button:has-text('全部下载')").first
            if all_download_btn.count() > 0 and all_download_btn.is_visible(timeout=3000):
                all_download_btn.click()
                logger.info("已点击'全部下载'按钮")
                time.sleep(2)  # 等待下拉菜单展开
                
                # 2. 点击"PDF"选项并下载 - 使用用户提供的CSS选择器
                pdf_selector = "#queryForm > div.panel-e.clearfix > div:nth-child(2) > div.panel-left.pull-left > div > ul > li:nth-child(2) > span"
                try:
                    pdf_btn = self.page.locator(pdf_selector).first
                    if pdf_btn.count() > 0 and pdf_btn.is_visible(timeout=3000):
                        # 直接使用JavaScript点击，避免弹窗阻塞
                        try:
                            pdf_btn.evaluate("el => el.click()")
                            logger.info("已点击'PDF'选项（使用JavaScript）")
                        except:
                            pdf_btn.click()
                            logger.info("已点击'PDF'选项")
                        
                        # 等待文件下载完成
                        time.sleep(3)
                        new_file = self._wait_and_find_new_file(before_files, timeout=60)
                        if new_file:
                            return self._move_to_download_path(new_file)
                except Exception as e:
                    logger.warning(f"点击PDF选项或下载失败: {e}")
                
                logger.warning("未找到'PDF'选项")
            else:
                logger.warning("未找到'全部下载'按钮，尝试其他选择器...")
            logger.warning("未找到全部下载按钮")
            return None
            
        except Exception as e:
            logger.error(f"点击打印/下载按钮失败: {e}")
            return None
    
    def _download_receipts(self) -> Optional[str]:
        """下载回单 - 备选方案"""
        logger.info("处理下载...")
        
        try:
            time.sleep(2)
            before_files = self._get_downloaded_files_before()
            download_start = time.time()
            
            # 处理可能的确认对话框
            for selector in ["button:has-text('确定')", "button:has-text('确认')"]:
                try:
                    btn = self.page.locator(selector).first
                    if btn.count() > 0 and btn.is_visible(timeout=2000):
                        btn.click()
                        time.sleep(2)
                        break
                except:
                    continue
            
            # 等待文件下载
            new_file = self._wait_and_find_new_file(before_files, timeout=60, start_time=download_start)
            if new_file:
                return self._move_to_download_path(new_file)
            
            logger.warning("未检测到新下载文件")
            return None
            
        except Exception as e:
            logger.error(f"下载回单失败: {e}")
            return None
    
    def _get_downloaded_files_before(self) -> dict:
        """获取多个下载目录中的文件列表（包括程序目录和系统下载目录）"""
        paths_to_check = [
            self.browser_download_path,
            Path.home() / "Downloads",
        ]
        
        all_files = {}
        for path in paths_to_check:
            if path.exists():
                all_files[path] = set(f.name for f in path.iterdir() if f.is_file())
            else:
                all_files[path] = set()
        
        return all_files
    
    def _is_guid_filename(self, filename: str) -> bool:
        """检查文件名是否是GUID格式（Chrome临时下载文件）"""
        import re
        # 匹配GUID格式: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
        guid_pattern = r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
        return bool(re.match(guid_pattern, filename))
    
    def _wait_and_find_new_file(self, before_files: dict, timeout: int = 60, start_time: float = None) -> Optional[Path]:
        """等待并查找新下载的PDF文件（支持多目录监控）"""
        start_time = start_time or time.time()
        
        # 合并所有目录的旧文件列表
        all_before_files = set()
        for path, files in before_files.items():
            all_before_files.update(files)
        
        logger.info(f"等待PDF下载完成，初始监控文件数: {len(all_before_files)}")
        
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
                        # 1. PDF文件
                        # 2. 大于1KB的文件
                        # 3. GUID格式的临时文件且大小合理
                        is_valid = (
                            file_name.endswith('.pdf') or
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
    
    def _move_to_download_path(self, source_file: Path) -> str:
        """将文件移动到归档目录，按指定格式重命名"""
        timestamp = datetime.now().strftime("%Y%m%d")
        ext = source_file.suffix or ".pdf"
        
        # 文件名格式: 网银名称_电子回单_yyyymmdd
        file_name = f"中信银行_电子回单_{timestamp}{ext}"
        # 使用归档路径（按年月分类）
        file_path = self.archive_path / file_name
        
        if file_path.exists():
            logger.info(f"文件已存在，将覆盖: {file_path}")
        
        shutil.move(str(source_file), str(file_path))
        logger.info(f"电子回单已保存: {file_path}")
        return str(file_path)
