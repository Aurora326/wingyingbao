# -*- coding: utf-8 -*-
"""
电子回单采集模块 - 工商银行适配（架子）
"""
import sys
import time
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict
from playwright.sync_api import Page
from logger import setup_logger

logger = setup_logger()

# 工行登录后通过“账户管理-电子回单”菜单进入，不直连 URL
RECEIPT_URL = ""


class ReceiptCollector:
    """电子回单采集器 - 工商银行适配"""

    def __init__(self, page: Page, config: Dict, statement_dates: Optional[Dict] = None):
        self.page = page
        self.root = page
        self.config = config
        self.download_path = Path(config.get('download_path', './downloads/receipts'))
        self.download_path.mkdir(parents=True, exist_ok=True)

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

        self.current_date = datetime.now()
        if config.get('archive_by_month', True):
            self.archive_path = self.download_path / f"{self.current_date.year}" / f"{self.current_date.month:02d}"
            self.archive_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"回单归档路径: {self.archive_path}")
            self._cleanup_old_archives()
        else:
            self.archive_path = self.download_path

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
                    if datetime(year, month, 1) < cutoff_date:
                        shutil.rmtree(month_dir)
                        deleted_count += 1
                        logger.info(f"已删除旧归档目录: {year}/{month:02d}")
                except (ValueError, OSError) as e:
                    logger.debug(f"处理目录 {month_dir} 时出错: {e}")

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
            if not self._navigate_to_receipt_page():
                logger.error("导航失败")
                return []

            self._set_query_conditions()

            if not self._click_query():
                logger.warning("点击查询按钮失败，尝试继续...")

            time.sleep(3)

            if not self._has_data():
                logger.info("当前查询条件下无电子回单数据")
                marker_file = self._create_no_data_marker()
                return [marker_file] if marker_file else []

            self._select_all_receipts()

            file_path = self._click_download()
            if not file_path:
                file_path = self._download_receipts()

            if file_path:
                downloaded_files.append(file_path)
        except Exception as e:
            logger.error(f"采集电子回单过程出错: {e}")

        logger.info(f"电子回单采集完成，共下载 {len(downloaded_files)} 个文件")
        return downloaded_files

    def _navigate_to_receipt_page(self) -> bool:
        """导航到电子回单查询页面（登录后点击“账户管理-电子回单”）"""
        logger.info("导航到电子回单查询页面...")
        time.sleep(2)

        if self._click_menu(["账户管理-电子回单", "电子回单查询", "电子回单", "回单查询"]):
            logger.info("已点击电子回单菜单，等待页面加载...")
            time.sleep(5)
            self._adopt_new_page()
            self.root = self._find_content_root()

            start = time.time()
            while time.time() - start < 30:
                self._adopt_new_page()
                self.root = self._find_content_root()
                if self._is_receipt_page():
                    logger.info("已进入电子回单查询页面")
                    self._close_sidebar_menu()
                    return True
                time.sleep(2)

            logger.warning("已点击菜单，但未识别到电子回单页面元素")

        if RECEIPT_URL:
            logger.info(f"尝试直接访问电子回单URL: {RECEIPT_URL}")
            try:
                self.page.goto(RECEIPT_URL, wait_until="domcontentloaded", timeout=30000)
                time.sleep(5)
                self.root = self._find_content_root()
                if self._is_receipt_page():
                    return True
            except Exception as e:
                logger.error(f"URL导航失败: {e}")

        return False

    def _click_menu(self, texts: List[str]) -> bool:
        """在页面及所有 iframe 中查找并点击菜单项"""
        for frame in self.page.frames:
            for text in texts:
                try:
                    loc = frame.get_by_text(text, exact=True).first
                    if loc.count() == 0 or not loc.is_visible():
                        loc = frame.get_by_text(text, exact=False).first
                    if loc.count() > 0 and loc.is_visible():
                        loc.click()
                        logger.info(f"已点击菜单: {text}")
                        return True
                except Exception as e:
                    logger.debug(f"点击菜单 {text} 失败: {e}")
        return False

    def _adopt_new_page(self):
        """点击菜单后若打开了新标签页，切换到新页面"""
        try:
            pages = self.page.context.pages
            if pages and pages[-1] != self.page:
                self.page = pages[-1]
                logger.info(f"已切换到新标签页: {self.page.url}")
        except Exception as e:
            logger.debug(f"切换新标签页失败: {e}")

    def _find_content_root(self):
        """在 iframe 中查找包含查询控件的业务页面"""
        for frame in self.page.frames:
            if frame == self.page.main_frame:
                continue
            try:
                if frame.locator("input[placeholder*='日期'], input[name*='date'], input[name*='Date']").count() > 0:
                    logger.info(f"找到业务 iframe: {frame.url}")
                    return frame
            except Exception:
                continue
        return self.page

    def _is_receipt_page(self) -> bool:
        """检查当前是否在电子回单页面（以日期控件为准）"""
        indicators = [
            "input[placeholder*='起始日期']",
            "input[placeholder*='开始日期']",
            "input[placeholder*='截止日期']",
            "input[placeholder*='结束日期']",
            "input[name='startDate']",
            "input[name='endDate']",
        ]
        for indicator in indicators:
            try:
                if self.root.locator(indicator).first.is_visible(timeout=1000):
                    return True
            except Exception:
                continue

        try:
            has_query = self.root.locator("button:has-text('查询')").first.is_visible(timeout=1000)
            has_table = self.root.locator("table").count() > 0
            return has_query and has_table
        except Exception:
            return False

    def _close_sidebar_menu(self):
        """关闭侧边栏 / 弹窗"""
        try:
            self.page.keyboard.press("Escape")
            time.sleep(0.5)
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
            # TODO: 按工行回单查询页日期控件调整
            self._set_date_field("startDate", self.start_date)
            self._set_date_field("endDate", self.end_date)
        except Exception as e:
            logger.warning(f"设置日期范围失败: {e}")

    def _set_date_field(self, field_name: str, date_value: str):
        """通过 JavaScript 设置日期字段"""
        try:
            self.root.evaluate(f"""
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
        # TODO: 按工行实际查询按钮调整
        selectors = [
            "button:has-text('查询')",
            "button[type='submit']",
            "#queryBtn",
            ".query-btn",
        ]
        for selector in selectors:
            try:
                btn = self.root.locator(selector).first
                if btn.count() > 0 and btn.is_visible(timeout=2000):
                    btn.click()
                    logger.info(f"已点击查询按钮: {selector}")
                    time.sleep(3)
                    return True
            except Exception:
                continue

        logger.warning("未找到查询按钮")
        return False

    def _has_data(self) -> bool:
        """检查是否有查询结果"""
        # TODO: 按工行实际空数据提示调整
        no_data_indicators = [
            "text=暂无数据",
            "text=无数据显示",
            "text=未查询到",
            "text=暂无符合条件的查询结果",
            ".no-data",
            ".empty",
        ]
        try:
            time.sleep(2)
            for indicator in no_data_indicators:
                try:
                    if self.root.locator(indicator).first.is_visible(timeout=2000):
                        logger.info("查询结果为空")
                        return False
                except Exception:
                    continue

            rows = self.root.locator("table tbody tr, .receipt-row, .data-row").all()
            if len(rows) > 0:
                logger.info(f"查询到 {len(rows)} 条记录")
            return True
        except Exception:
            return True

    def _create_no_data_marker(self) -> str:
        """创建无数据标记文件"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"工商银行_电子回单_无数据_{timestamp}.txt"
        file_path = self.archive_path / file_name

        content = f"""工商银行企业网银 - 电子回单采集结果
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

    def _select_all_receipts(self):
        """全选回单"""
        logger.info("全选回单...")
        try:
            checkbox = self.root.locator("th input[type='checkbox']").first
            if checkbox.count() > 0 and checkbox.is_visible(timeout=2000):
                checkbox.click()
                time.sleep(1)
            else:
                logger.warning("未找到全选框")
        except Exception as e:
            logger.warning(f"全选回单失败: {e}")

    def _click_download(self) -> Optional[str]:
        """点击下载/打印按钮并等待文件（主方案）"""
        logger.info("点击下载/打印按钮...")
        before_files = self._get_downloaded_files_before()

        try:
            # TODO: 按工行实际回单下载入口调整
            for selector in ["button:has-text('全部下载')", "button:has-text('下载')", "button:has-text('打印')"]:
                try:
                    btn = self.root.locator(selector).first
                    if btn.count() > 0 and btn.is_visible(timeout=3000):
                        btn.click()
                        logger.info(f"已点击按钮: {selector}")
                        time.sleep(2)
                        break
                except Exception:
                    continue
            else:
                logger.warning("未找到下载/打印按钮")
                return None

            # 下拉选项（PDF / 回单）
            for selector in ["text=PDF", "text=PDF下载", "text=电子回单"]:
                try:
                    option = self.root.locator(selector).first
                    if option.count() > 0 and option.is_visible(timeout=2000):
                        option.evaluate("el => el.click()")
                        logger.info(f"已选择回单格式: {selector}")
                        break
                except Exception:
                    continue

            time.sleep(3)
            new_file = self._wait_and_find_new_file(before_files, timeout=60)
            if new_file:
                return self._move_to_download_path(new_file)
            return None
        except Exception as e:
            logger.error(f"点击下载/打印按钮失败: {e}")
            return None

    def _download_receipts(self) -> Optional[str]:
        """下载回单 - 备选方案"""
        logger.info("处理下载...")
        try:
            time.sleep(2)
            before_files = self._get_downloaded_files_before()
            download_start = time.time()

            for selector in ["button:has-text('确定')", "button:has-text('确认')"]:
                try:
                    btn = self.root.locator(selector).first
                    if btn.count() > 0 and btn.is_visible(timeout=2000):
                        btn.click()
                        time.sleep(2)
                        break
                except Exception:
                    continue

            new_file = self._wait_and_find_new_file(before_files, timeout=60, start_time=download_start)
            if new_file:
                return self._move_to_download_path(new_file)

            logger.warning("未检测到新下载文件")
            return None
        except Exception as e:
            logger.error(f"下载回单失败: {e}")
            return None

    def _get_downloaded_files_before(self) -> dict:
        """获取下载目录中的初始文件列表"""
        paths_to_check = [
            self.browser_download_path,
            Path.home() / "Downloads",
        ]

        all_files = {}
        for path in paths_to_check:
            all_files[path] = set(f.name for f in path.iterdir() if f.is_file()) if path.exists() else set()
        return all_files

    def _is_guid_filename(self, filename: str) -> bool:
        """检查文件名是否是 GUID 格式"""
        import re
        guid_pattern = r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
        return bool(re.match(guid_pattern, filename))

    def _wait_and_find_new_file(self, before_files: dict, timeout: int = 60,
                                start_time: float = None) -> Optional[Path]:
        """等待并查找新下载的文件"""
        start_time = start_time or time.time()

        all_before_files = set()
        for files in before_files.values():
            all_before_files.update(files)

        logger.info(f"等待下载完成，初始监控文件数: {len(all_before_files)}")

        download_dirs = [
            self.browser_download_path,
            Path.home() / "Downloads",
        ]

        while time.time() - start_time < timeout:
            time.sleep(1)

            for download_dir in download_dirs:
                if not download_dir.exists():
                    continue

                for file_path in download_dir.iterdir():
                    if not file_path.is_file() or file_path.name in all_before_files:
                        continue

                    try:
                        file_size = file_path.stat().st_size
                    except Exception:
                        file_size = 0

                    logger.info(f"发现新文件: {file_path.name} (大小: {file_size} bytes) 在目录: {download_dir}")

                    is_valid = (
                        file_path.name.endswith('.pdf') or
                        file_size > 1000 or
                        (self._is_guid_filename(file_path.name) and file_size > 100)
                    )

                    if is_valid:
                        logger.info(f"确认新下载文件: {file_path}")
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
                    if mtime > start_time and mtime > newest_mtime:
                        newest = f
                        newest_mtime = mtime
                except Exception:
                    continue

        if newest:
            logger.info(f"找到最新文件作为备选: {newest}")
        return newest

    def _move_to_download_path(self, source_file: Path) -> str:
        """移动到归档目录并按规范重命名"""
        timestamp = datetime.now().strftime("%Y%m%d")
        ext = source_file.suffix or ".pdf"
        file_name = f"工商银行_电子回单_{timestamp}{ext}"
        file_path = self.archive_path / file_name

        if file_path.exists():
            logger.info(f"文件已存在，将覆盖: {file_path}")

        shutil.move(str(source_file), str(file_path))
        logger.info(f"电子回单已保存: {file_path}")
        return str(file_path)
