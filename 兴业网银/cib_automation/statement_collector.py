# -*- coding: utf-8 -*-
"""
银行流水采集模块
"""
import os
import re
import time
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Union
from playwright.sync_api import Page, Frame
from logger import setup_logger
from config_loader import StatementsConfig

logger = setup_logger()


class StatementCollector:
    """银行流水采集器"""
    
    def __init__(self, page: Page, config: StatementsConfig):
        self.page = page
        self.config = config
        self.download_path = Path(config.download_path)
        self.download_path.mkdir(parents=True, exist_ok=True)
        self.main_frame: Union[Page, Frame] = page
        
        # 浏览器默认下载目录
        self.browser_download_path = Path(__file__).parent / "downloads"
        
    def _find_main_frame(self) -> Union[Page, Frame]:
        """找到包含查询元素的 iframe"""
        if self.page.locator("input#startDate").count() > 0:
            return self.page
        
        for frame in self.page.frames:
            if frame == self.page.main_frame:
                continue
            try:
                if frame.locator("input#startDate").count() > 0:
                    return frame
            except:
                continue
        
        return self.page
    
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
            self._navigate_to_statement_page()
            self.main_frame = self._find_main_frame()
            
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
        
        # 在 iframe 中查找 a#queryUrl
        for frame in self.page.frames:
            if frame == self.page.main_frame:
                continue
            try:
                locator = frame.locator("a#queryUrl")
                if locator.count() > 0:
                    locator.first.click()
                    logger.info("点击首页快捷入口: 查询明细")
                    time.sleep(3)
                    return
            except:
                continue
        
        raise Exception("未找到查询明细入口")
    
    def _get_all_accounts(self) -> List[str]:
        """获取所有可用账户"""
        logger.info("正在获取账户列表...")
        accounts = []
        
        try:
            # 点击下拉框
            dropdown = self.main_frame.locator("text=查询账户").first
            if dropdown.is_visible(timeout=3000):
                dropdown.click()
                time.sleep(1)
                
                options = self.main_frame.locator(".el-select-dropdown__item").all()
                for option in options:
                    text = option.text_content()
                    if text and text.strip():
                        accounts.append(text.strip())
        except Exception as e:
            logger.debug(f"获取账户列表失败: {e}")
        
        if not accounts:
            return [""]
        
        return accounts
    
    def _check_has_data(self) -> bool:
        """检查查询结果是否有数据"""
        try:
            no_data = self.main_frame.locator("text=无数据显示")
            if no_data.count() > 0 and no_data.first.is_visible(timeout=2000):
                return False
            return True
        except:
            return True
    
    def _get_downloaded_files_before(self) -> set:
        """获取当前浏览器下载目录中的文件列表"""
        if not self.browser_download_path.exists():
            return set()
        return set(f.name for f in self.browser_download_path.iterdir() if f.is_file())
    
    def _wait_and_find_new_file(self, before_files: set, timeout: int = 30) -> Optional[Path]:
        """等待并查找新下载的文件"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            time.sleep(1)
            if not self.browser_download_path.exists():
                continue
            current_files = set(f.name for f in self.browser_download_path.iterdir() if f.is_file())
            new_files = current_files - before_files
            if new_files:
                # 找到新文件，等待文件下载完成（检查文件大小是否还在变化）
                new_file_name = new_files.pop()
                new_file_path = self.browser_download_path / new_file_name
                # 等待文件稳定（大小不再变化）
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
        return None
    
    def _move_to_download_path(self, source_file: Path, account: str, end_date: str) -> str:
        """将文件移动到配置的下载目录，按指定格式重命名"""
        from datetime import datetime
        
        # 获取当前日期: yyyymmdd
        timestamp = datetime.now().strftime("%Y%m%d")
        
        # 获取文件扩展名
        ext = source_file.suffix or ".xlsx"
        
        # 文件名格式: 网银名称_银行流水_yyyymmdd
        file_name = f"兴业银行_银行流水_{timestamp}{ext}"
        file_path = self.download_path / file_name
        
        # 如果文件已存在，直接覆盖
        if file_path.exists():
            logger.info(f"文件已存在，将覆盖: {file_path}")
        
        shutil.move(str(source_file), str(file_path))
        logger.info(f"流水文件已保存: {file_path}")
        return str(file_path)
    
    def _download_statement(self, account: str, start_date: str, end_date: str) -> Optional[str]:
        """下载单个账户的流水"""
        logger.info(f"正在处理账户 {account} 的流水...")
        
        # 选择账户
        if account:
            try:
                self.main_frame.locator(f"text={account}").first.click()
                time.sleep(0.5)
            except:
                pass
        
        # 设置日期范围
        try:
            self.main_frame.evaluate(f"""
                document.querySelector('input#startDate').value = '{start_date}';
                document.querySelector('input#endDate').value = '{end_date}';
                document.querySelector('input#startDate').dispatchEvent(new Event('change', {{ bubbles: true }}));
                document.querySelector('input#endDate').dispatchEvent(new Event('change', {{ bubbles: true }}));
            """)
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"设置日期范围失败: {e}")
        
        # 点击查询按钮
        try:
            self.main_frame.locator("input#queryOpeBtn").first.click()
            logger.info("点击查询按钮")
            time.sleep(3)
        except Exception as e:
            logger.error(f"点击查询按钮失败: {e}")
            return None
        
        # 检查是否有数据
        if not self._check_has_data():
            logger.info("当前运行结果为空")
            return "当前运行结果为空"
        
        logger.info("检测到数据，开始下载...")
        
        # 点击下载按钮
        try:
            self.main_frame.locator("text=下载").first.click()
            time.sleep(1)
            
            if self.config.file_format.lower() == "excel":
                self.main_frame.locator("text=Excel下载").first.click()
            else:
                self.main_frame.locator("text=PDF下载").first.click()
            
            time.sleep(2)
        except Exception as e:
            logger.error(f"点击下载按钮失败: {e}")
            return None
        
        # 处理确认弹窗（确定要下载全部XX条明细吗？）
        try:
            time.sleep(2)
            # 在所有frames中查找"确定"按钮
            for frame in self.page.frames:
                try:
                    confirm_btn = frame.locator(".ui-dialog-buttonpane button:nth-child(2), button:has-text('确定'):visible").first
                    if confirm_btn.is_visible(timeout=3000):
                        confirm_btn.click()
                        logger.info("点击确认下载弹窗")
                        break
                except:
                    continue
        except Exception as e:
            logger.debug(f"处理确认弹窗失败: {e}")
        
        # 等待生成Excel弹窗并点击下载链接
        logger.info("等待Excel文件生成完成...")
        
        try:
            # 查找"打包下载已生成文件"链接，并等待生成完成
            link = None
            max_wait_time = 120  # 最大等待120秒
            start_time = time.time()
            
            while time.time() - start_time < max_wait_time:
                for frame in self.page.frames:
                    try:
                        # 检查是否还在生成中（有倒计时提示）
                        page_text = frame.content()
                        if "再次下载请等待" in page_text:
                            # 提取倒计时秒数
                            import re
                            match = re.search(r'再次下载请等待[^\d]*(\d+)', page_text)
                            if match:
                                wait_seconds = int(match.group(1))
                                logger.info(f"文件生成中，还需等待约 {wait_seconds} 秒...")
                                time.sleep(min(wait_seconds + 2, 10))  # 等待倒计时结束+缓冲
                                continue
                        
                        # 查找下载链接
                        link = frame.locator("a:has-text('打包下载已生成文件'), a[href*='downloadAll']").first
                        if link.is_visible(timeout=2000):
                            # 检查链接是否可点击（不是禁用状态）
                            is_disabled = link.evaluate("el => el.disabled || el.classList.contains('disabled')")
                            if not is_disabled:
                                logger.info("Excel文件已生成完成，准备下载")
                                break
                            else:
                                logger.info("下载链接还在禁用状态，继续等待...")
                    except:
                        continue
                
                if link:
                    break
                    
                time.sleep(2)
            
            if not link:
                logger.error("未找到下载链接或等待超时")
                return None
            
            # 记录下载前的文件列表
            before_files = self._get_downloaded_files_before()
            
            # 尝试使用Playwright监听下载
            try:
                with self.page.expect_download(timeout=30000) as download_info:
                    link.click()
                    logger.info("已点击下载链接（监听模式）")
                
                download = download_info.value
                # 检查下载的文件扩展名，确保是Excel文件而不是HTML
                suggested_name = download.suggested_filename
                if suggested_name.endswith('.html') or suggested_name.endswith('.htm'):
                    logger.warning(f"下载的是HTML文件({suggested_name})，可能是文件还未生成完成")
                    # 取消这个下载，等待更长时间再试
                    download.cancel()
                    time.sleep(5)
                    return self._download_statement(account, start_date, end_date)  # 递归重试
                
                # 先保存到临时位置，然后重命名
                temp_path = self.browser_download_path / suggested_name
                download.save_as(str(temp_path))
                # 使用统一的重命名规则
                result_path = self._move_to_download_path(temp_path, account, end_date)
                # 关闭生成Excel弹窗
                self._close_excel_dialog()
                return result_path
                
            except Exception as e:
                # Playwright监听失败，使用备选方案
                logger.warning(f"Playwright监听下载失败，使用备选方案: {e}")
                link.click()
                logger.info("已点击下载链接（直接点击模式）")
                
                # 等待文件下载完成
                time.sleep(3)  # 给浏览器一些时间开始下载
                new_file = self._wait_and_find_new_file(before_files, timeout=30)
                if new_file:
                    # 检查文件类型
                    if new_file.suffix in ['.html', '.htm']:
                        logger.warning(f"下载的是HTML文件({new_file.name})，可能是文件还未生成完成")
                        new_file.unlink()  # 删除HTML文件
                        time.sleep(5)
                        return self._download_statement(account, start_date, end_date)  # 递归重试
                    result = self._move_to_download_path(new_file, account, end_date)
                    # 关闭生成Excel弹窗
                    self._close_excel_dialog()
                    return result
                else:
                    logger.warning("未检测到新下载的文件")
                    return "已触发下载，请检查浏览器下载目录"
                            
        except Exception as e:
            logger.error(f"下载过程出错: {e}")
            return None
    
    def _close_excel_dialog(self):
        """关闭生成Excel弹窗"""
        try:
            logger.info("正在关闭生成Excel弹窗...")
            # 查找关闭按钮（X）
            for frame in self.page.frames:
                try:
                    # 方法1: 查找关闭按钮
                    close_btn = frame.locator(".ui-dialog-titlebar-close, .dialog-close, button[title='关闭']").first
                    if close_btn.is_visible(timeout=2000):
                        close_btn.click()
                        logger.info("已关闭Excel生成弹窗")
                        time.sleep(1)
                        return
                except:
                    continue
            
            # 方法2: 按ESC键关闭
            try:
                self.page.keyboard.press("Escape")
                logger.info("已按ESC键尝试关闭弹窗")
                time.sleep(1)
            except:
                pass
                
        except Exception as e:
            logger.debug(f"关闭Excel弹窗失败（可能已关闭）: {e}")
