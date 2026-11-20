# -*- coding: utf-8 -*-
"""
银行流水采集模块 - 建设银行适配版

注意：建设银行页面结构可能因版本更新而变化，
如果采集失败，请检查页面元素选择器是否需要更新。
"""
import sys
import time
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from playwright.sync_api import Page
from logger import setup_logger
from common.download_helper import (
    cleanup_old_archives,
    ensure_archive_dir,
    get_downloaded_files_before,
    resolve_browser_download_dir,
    wait_and_find_new_file,
)
from common.frame_helper import find_frame_containing
from config_loader import StatementsConfig
from popup_handler import PopupHandlerMixin
from account_detail import CCBAccountDetailPage

logger = setup_logger()


class StatementCollector(PopupHandlerMixin):
    """银行流水采集器 - 建设银行适配版"""
    
    def __init__(self, page: Page, config: StatementsConfig):
        self.page = page
        self.config = config
        self.detail = CCBAccountDetailPage(page)
        self.download_path = Path(config.download_path)
        self.download_path.mkdir(parents=True, exist_ok=True)
        
        # 调试：输出配置值
        logger.info(f"[DEBUG] StatementsConfig file_format: {getattr(config, 'file_format', 'NOT_SET')}")
        
        # 设置归档路径（按年月）并清理过期归档
        self.current_date = datetime.now()
        self.archive_path = ensure_archive_dir(
            self.download_path, bool(config.archive_by_month), self.current_date
        )
        if config.archive_by_month:
            logger.info(f"流水归档路径: {self.archive_path}")
            cleanup_old_archives(self.download_path, config.retention_months, "流水", self.current_date)

        # 支持打包环境：frozen 用 exe 所在目录，开发环境用脚本目录
        env_label = "打包环境" if getattr(sys, "frozen", False) else "开发环境"
        self.browser_download_path = resolve_browser_download_dir(Path(__file__).resolve().parent)
        logger.info(f"[{env_label}] 浏览器下载路径: {self.browser_download_path}")
    
    def _find_frame_containing(self, selectors: list, timeout: int = 10, match_all: bool = False):
        """在所有 frame（包括 iframe）中查找包含目标选择器的 frame，优先返回元素可见的。"""
        return find_frame_containing(
            self.page, selectors, timeout=timeout, match_all=match_all, prefer_visible=True
        )
    

    

    def collect(self, accounts: Optional[List[str]] = None) -> List[str]:
        """采集银行流水 - CCB全选账户后一次性下载"""
        downloaded_files = []

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
            logger.info(f"查询流水 {start_date_str} 至 {end_date_str}")
            file_path = self._download_statement("", start_date_str, end_date_str)
            if file_path and "无数据" in Path(file_path).name:
                # 无数据标记交给 run_task 转成 no_data=true
                downloaded_files.append(file_path)
            elif file_path and Path(file_path).exists():
                downloaded_files.append(file_path)
            else:
                logger.info(f"{start_date_str} 至 {end_date_str} 无流水数据")
        except Exception as e:
            logger.error(f"采集流水失败: {e}")
            raise

        logger.info(f"流水采集完成，共下载 {len(downloaded_files)} 个文件")
        return downloaded_files
    
    
    def _is_statement_page(self) -> bool:
        """检查当前是否在交易明细相关页面 - 支持iframe"""
        contexts = [self.page] + list(self.page.frames)
        
        indicators = [
            "#StDt", "#EdDt",
            "text=交易明细查询",
            "#dlAll",
            "input[name='Submit3']",
            "input[name='Submit1']",
        ]
        
        for context in contexts:
            for indicator in indicators:
                try:
                    locator = context.locator(indicator).first
                    if locator.count() > 0 and locator.is_visible(timeout=500):
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
                "select[name='acctNo']",
                ".el-select",
                "text=请选择账户",
                "text=请选择账号",
                "#accountSelect",
                "#acctNo",
                ".dropdown-toggle",
            ]
            
            for selector in dropdown_selectors:
                try:
                    dropdown = self.page.locator(selector).first
                    if dropdown.is_visible(timeout=3000):
                        dropdown.click()
                        time.sleep(1)
                        
                        # 获取选项
                        options = self.page.locator(
                            ".el-select-dropdown__item, option, .dropdown-item, "
                            ".account-option, .select-option"
                        ).all()
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
        file_name = f"建设银行_银行流水_无数据_{timestamp}.txt"
        # 使用归档路径（按年月分类）
        file_path = self.archive_path / file_name
        
        content = f"""建设银行企业网银 - 银行流水采集结果
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
        """是否有交易：结果页分页总数 #HZDIV .curPageNum 为 0 即无数据。"""
        return not self.detail.is_empty_result()
    
    def _get_downloaded_files_before(self):
        return get_downloaded_files_before(self.browser_download_path)

    def _wait_and_find_new_file(self, before_files, timeout: int = 60):
        return wait_and_find_new_file(before_files, timeout)

    @staticmethod
    def _detect_ext(path: Path) -> str:
        """按文件头判断真实格式：建行“Excel下载”实际给的是旧版 xls。"""
        try:
            magic = path.read_bytes()[:8]
        except Exception:
            return ".xlsx"
        if magic.startswith(b"\xd0\xcf\x11\xe0"):
            return ".xls"
        if magic.startswith(b"PK"):
            return ".xlsx"
        return ".html"

    def _move_to_download_path(self, source_file: Path, account: str, end_date: str) -> str:
        """将文件移动到归档目录，按指定格式重命名"""
        ext = source_file.suffix or self._detect_ext(source_file)
        
        # 检查配置文件中的格式设置
        file_format = getattr(self.config, 'file_format', 'excel').lower()
        
        # 如果配置为 xls，修改文件后缀（直接重命名，不转换格式）
        if file_format == 'xls' and ext == '.xlsx':
            new_path = source_file.with_suffix('.xls')
            source_file.rename(new_path)
            source_file = new_path
            ext = '.xls'
            logger.info(f"已将文件后缀修改为 .xls: {source_file}")
        
        # 文件名格式: 网银名称_银行流水_分段结束日期（分段下载时避免互相覆盖）
        date_tag = (end_date or datetime.now().strftime("%Y-%m-%d")).replace("-", "")
        file_name = f"建设银行_银行流水_{date_tag}{ext}"
        # 使用归档路径（按年月分类）
        file_path = self.archive_path / file_name
        
        if file_path.exists():
            logger.info(f"文件已存在，将覆盖: {file_path}")
        
        shutil.move(str(source_file), str(file_path))
        logger.info(f"流水文件已保存: {file_path}")
        return str(file_path)
    
    
    
    def _download_statement(self, account: str, start_date: str, end_date: str) -> Optional[str]:
        """下载流水 - CCB全选账户后一次性下载"""
        logger.info("开始下载交易明细...")

        result_frame = self.detail.open_and_query(start_date, end_date)
        if not result_frame:
            # 检查是否无数据
            if not self._check_has_data():
                logger.info("查询结果为空")
                return self._create_no_data_marker(start_date, end_date)
            raise Exception("查询结果页加载失败")

        self._work_frame = self.detail.work_frame
        frame = result_frame
        logger.info("查询结果已加载")
        
        # 再次检查是否有数据
        if not self._check_has_data():
            logger.info("查询结果为空")
            return self._create_no_data_marker(start_date, end_date)
        
        # 下载全部 -> Excel下载
        logger.info("打开下载全部菜单...")
        download_btn = frame.locator('#dlAll').first
        if download_btn.count() == 0:
            download_btn = frame.locator('a.tableRBtn.download').first
        if download_btn.count() == 0:
            download_btn = frame.locator('text=下载全部').first

        if download_btn.count() == 0:
            raise Exception("未找到下载全部按钮")
        
        before_files = self._get_downloaded_files_before()

        dialog_messages = []

        def _on_dialog(dialog):
            dialog_messages.append(dialog.message)
            logger.warning(f"页面弹窗: {dialog.message}")
            try:
                dialog.accept()
            except Exception:
                pass

        self.page.on("dialog", _on_dialog)
        try:
            with self.page.expect_download(timeout=60000) as download_info:
                # 先 hover 下载全部按钮，显示下拉菜单
                try:
                    download_btn.hover(timeout=3000)
                    time.sleep(1)
                except Exception:
                    pass
                
                # 使用 JS 强制点击 "Excel下载"，绕过 Playwright 可见性检查
                click_result = frame.evaluate("""
                    () => {
                        // 遍历所有 li，按文本内容匹配
                        let excelLi = null;
                        const allLi = document.querySelectorAll('li');
                        for (const li of allLi) {
                            if (li.textContent.trim().includes('Excel下载')) {
                                excelLi = li;
                                break;
                            }
                        }
                        if (!excelLi) {
                            // 尝试匹配 onclick 包含 submitSelect 且文本包含 Excel下载 的 li
                            const allLi2 = document.querySelectorAll('li[onclick*="submitSelect"]');
                            for (const li of allLi2) {
                                if (li.textContent.trim().includes('Excel下载')) {
                                    excelLi = li;
                                    break;
                                }
                            }
                        }
                        if (!excelLi) {
                            // 最后再尝试 a 标签
                            const allA = document.querySelectorAll('a');
                            for (const a of allA) {
                                if (a.textContent.trim().includes('Excel下载')) {
                                    excelLi = a;
                                    break;
                                }
                            }
                        }
                        if (excelLi) {
                            excelLi.scrollIntoView({block: 'center'});
                            excelLi.click();
                            return 'clicked:' + excelLi.outerHTML.substring(0, 100);
                        }
                        return 'not_found';
                    }
                """)
                logger.info(f"JS 点击 Excel下载 结果: {click_result}")
                
                if click_result == 'not_found':
                    raise Exception("未找到 Excel下载 选项")
                
                time.sleep(2)
                if dialog_messages:
                    logger.warning(f"下载点击后页面提示: {' | '.join(dialog_messages)}")
                try:
                    shot = self.browser_download_path / "after_download_click.png"
                    self.page.screenshot(path=str(shot))
                    logger.info(f"下载点击后截图: {shot}")
                except Exception:
                    pass
            download = download_info.value
            temp_path = self.browser_download_path / download.suggested_filename
            download.save_as(str(temp_path))
            return self._move_to_download_path(temp_path, account, end_date)
        except Exception as e:
            logger.warning(f"Playwright download监听失败，使用文件轮询: {e}")
            new_file = self._wait_and_find_new_file(before_files, timeout=120)
            if new_file:
                return self._move_to_download_path(new_file, account, end_date)
            if dialog_messages:
                raise Exception(f"下载失败：{dialog_messages[0]}")
            raise Exception("下载失败：未找到文件")
        finally:
            try:
                self.page.remove_listener("dialog", _on_dialog)
            except Exception:
                pass
