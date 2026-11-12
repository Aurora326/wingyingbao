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
from common.download_helper import get_downloaded_files_before, wait_and_find_new_file
from config_loader import StatementsConfig

logger = setup_logger()


class StatementCollector:
    """银行流水采集器 - 建设银行适配版"""
    
    def __init__(self, page: Page, config: StatementsConfig):
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
    
    def _find_frame_containing(self, selectors: list, timeout: int = 10, match_all: bool = False):
        """在所有 frame（包括 iframe）中查找包含目标选择器的 frame，优先返回元素可见的。"""
        start = time.time()
        while time.time() - start < timeout:
            fallback = None
            for frame in self.page.frames:
                try:
                    if match_all:
                        if all(frame.locator(selector).count() > 0 for selector in selectors):
                            return frame
                    else:
                        for selector in selectors:
                            loc = frame.locator(selector).first
                            if loc.count() == 0:
                                continue
                            if fallback is None:
                                fallback = frame
                            try:
                                if loc.is_visible(timeout=200):
                                    return frame
                            except Exception:
                                pass
                except Exception:
                    continue
            if fallback is not None:
                return fallback
            time.sleep(0.5)
        return None
    
    def _to_ccb_date(self, date_str: str) -> str:
        """将 YYYY-MM-DD 转换为 CCB 需要的 YYYYMMDD"""
        return date_str.replace('-', '')

    def _log_frame_diagnostics(self, tag: str = "frame诊断"):
        """输出各 frame 中关键元素的探测结果。"""
        try:
            contexts = [("page", self.page)] + [
                (f"frame[{idx}]", frame) for idx, frame in enumerate(self.page.frames)
            ]
            for label, context in contexts:
                try:
                    info = context.evaluate("""
                        () => ({
                            url: location.href,
                            hasStDt: !!document.querySelector('#StDt'),
                            hasEdDt: !!document.querySelector('#EdDt'),
                            hasSubmit4: !!document.querySelector('input[name="Submit4"]'),
                            hasSubmit4Confirm: !!document.querySelector('input[type="submit"][name="Submit4"][value="确定"]'),
                            hasConfirmText: !!document.querySelector('input[value="确定"]')
                        })
                    """)
                    logger.info(f"{tag} {label}: {info}")
                except Exception as e:
                    logger.debug(f"{tag} {label} 失败: {e}")
        except Exception as e:
            logger.debug(f"{tag} 失败: {e}")

    def _click_query_confirm_button(self) -> str:
        """点击交易明细查询页“确定”按钮。"""
        self._log_frame_diagnostics("确定按钮前")

        contexts = []
        if getattr(self, "_work_frame", None):
            contexts.append(("work_frame", self._work_frame))

        for idx, frame in enumerate(self.page.frames):
            if frame == getattr(self, "_work_frame", None):
                continue
            contexts.append((f"frame[{idx}]", frame))
        contexts.append(("page", self.page))

        confirm_selectors = [
            'input[type="submit"][name="Submit4"][value="确定"]',
            'input[name="Submit4"]',
            'input[type="submit"][value="确定"]',
            'input[value="确定"]',
        ]

        for label, context in contexts:
            for selector in confirm_selectors:
                try:
                    result = context.evaluate(
                        """(selector) => {
                            const btn = document.querySelector(selector);
                            if (!btn) return null;
                            const style = window.getComputedStyle(btn);
                            const visible = style.display !== 'none' &&
                                style.visibility !== 'hidden' &&
                                btn.offsetWidth > 0 &&
                                btn.offsetHeight > 0;
                            if (!visible) return `found_not_visible:${selector}`;
                            btn.scrollIntoView({block: 'center'});
                            btn.click();
                            if (btn.form) {
                                try { btn.form.submit(); } catch (e) {}
                            }
                            return `clicked:${selector}`;
                        }""",
                        selector,
                    )
                    if result and result.startswith("clicked:"):
                        logger.info(f"确定按钮点击结果: {label}:{result}")
                        return f"{label}:{result}"
                    if result:
                        logger.info(f"确定按钮探测结果: {label}:{result}")
                except Exception as e:
                    logger.debug(f"点击确定按钮失败 {label} {selector}: {e}")

        raise Exception("未找到确定按钮")
    
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
            self._navigate_to_statement_page()
            logger.info(f"查询流水 {start_date_str} 至 {end_date_str}")
            file_path = self._download_statement("", start_date_str, end_date_str)
            if file_path and Path(file_path).exists() and "无数据" not in Path(file_path).name:
                downloaded_files.append(file_path)
            else:
                logger.info(f"{start_date_str} 至 {end_date_str} 无流水数据")
        except Exception as e:
            logger.error(f"采集流水失败: {e}")
            raise

        logger.info(f"流水采集完成，共下载 {len(downloaded_files)} 个文件")
        return downloaded_files
    
    def _close_ad_popup(self, timeout: float = 15.0) -> bool:
        """关闭登录后弹出的首页广告弹窗（#befADV1），轮询等待它出现。"""
        selector = "#befADV1 > img:nth-child(2)"
        script = """s => {
            const el = document.querySelector(s);
            if (!el) return false;
            el.click();
            return true;
        }"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for context in [self.page] + list(self.page.frames):
                try:
                    if context.evaluate(script, selector):
                        logger.info("已关闭首页广告弹窗")
                        time.sleep(1)
                        return True
                except Exception:
                    continue
            time.sleep(0.5)
        return False

    def _navigate_to_statement_page(self):
        """导航到交易明细查询页面 - 按CCB实际页面结构"""
        logger.info("导航到交易明细查询页面...")
        time.sleep(2)

        # 先关闭可能的弹窗
        try:
            self.page.keyboard.press("Escape")
            time.sleep(0.5)
        except Exception:
            pass

        if not self._close_ad_popup():
            logger.info("未发现首页广告弹窗")
        
        # Step 1: 在首页点击"账户查询"链接（在iframe中）
        frame = self._find_frame_containing(['#toAccQuery'], timeout=10)
        if not frame:
            raise Exception("未找到账户查询链接(#toAccQuery)")
        
        logger.info("点击账户查询...")
        link = frame.locator('#toAccQuery').first
        try:
            link.click(timeout=5000)
        except Exception as e:
            logger.warning(f"常规点击账户查询失败（{e}），改用 JS 点击")
            frame.evaluate(
                "() => { const el = document.querySelector('#toAccQuery'); if (el) el.click(); }"
            )
        time.sleep(3)
        
        # Step 2: 等待账户列表页，勾选"全选"
        frame = self._find_frame_containing(['#all_0'], timeout=10)
        if not frame:
            raise Exception("未进入账户列表页")
        
        logger.info("勾选全选账户...")
        all_cb = frame.locator('#all_0').first
        if all_cb.count() > 0:
            try:
                if not all_cb.is_checked():
                    all_cb.check()
            except Exception as e:
                logger.warning(f"常规勾选全选失败（{e}），改用 JS 点击")
                frame.evaluate(
                    "() => { const el = document.querySelector('#all_0');"
                    " if (el && !el.checked) el.click(); }"
                )
        time.sleep(1)
        
        # Step 3: 点击"交易明细"按钮
        logger.info("点击交易明细...")
        detail_btn = frame.locator('input[name="Submit3"]').first
        if detail_btn.count() == 0:
            detail_btn = frame.locator('input[onclick*="detailQuery"]').first
        if detail_btn.count() == 0:
            detail_btn = frame.locator('input[value="交易明细"]').first
        
        if detail_btn.count() == 0:
            raise Exception("未找到交易明细按钮")
        
        detail_btn.click()
        time.sleep(3)
        
        # Step 4: 等待进入交易明细查询条件页
        frame = self._find_frame_containing(
            ['#StDt', '#EdDt', 'input[name="Submit4"]'],
            timeout=10,
            match_all=True,
        )
        if not frame:
            raise Exception("未进入交易明细查询页")
        
        self._work_frame = frame
        logger.info("成功进入交易明细查询页面")
    
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
        """检查查询结果是否有数据 - 在iframe中查找"""
        frame = getattr(self, '_work_frame', None)
        if not frame:
            frame = self._find_frame_containing(['#dlAll'], timeout=5)
        
        if not frame:
            return True
        
        no_data_indicators = [
            "text=无数据显示", "text=暂无数据", "text=未查询到",
            "text=暂无符合条件的查询结果", "text=没有记录", "text=无数据",
        ]
        for indicator in no_data_indicators:
            try:
                if frame.locator(indicator).first.is_visible(timeout=2000):
                    return False
            except Exception:
                continue
        return True
    
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
    
    def _set_date_range(self, start_date: str, end_date: str):
        """设置日期范围 - CCB 使用 YYYYMMDD 格式"""
        logger.info(f"设置日期范围: {start_date} 至 {end_date}")
        
        frame = getattr(self, '_work_frame', None)
        if not frame:
            frame = self._find_frame_containing(
                ['#StDt', '#EdDt', 'input[name="Submit4"]'],
                timeout=5,
                match_all=True,
            )
            self._work_frame = frame
        
        if not frame:
            raise Exception("未找到交易明细查询页")
        
        start_ccb = self._to_ccb_date(start_date)
        end_ccb = self._to_ccb_date(end_date)
        
        self._set_date_field_ccb(frame, '#StDt', start_ccb)
        self._set_date_field_ccb(frame, '#EdDt', end_ccb)
        
        # 关闭可能弹出的日历
        self.page.keyboard.press("Escape")
        time.sleep(0.3)
        
        logger.info(f"日期已设置: {start_ccb} 至 {end_ccb}")
    
    def _set_date_field_by_playwright(self, field_id: str, date_value: str, page_or_frame=None):
        """使用 Playwright 直接设置日期 - 针对建设银行日期选择器优化"""
        try:
            context = page_or_frame or self.page
            input_field = context.locator(f"#{field_id}").first
            
            if input_field.count() == 0:
                # 尝试其他选择器
                input_field = context.locator(f"input[name='{field_id}']").first
            
            if input_field.count() == 0:
                logger.warning(f"{field_id} 元素不存在")
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
    
    def _download_statement(self, account: str, start_date: str, end_date: str) -> Optional[str]:
        """下载流水 - CCB全选账户后一次性下载"""
        logger.info("开始下载交易明细...")
        
        # 设置日期
        self._set_date_range(start_date, end_date)

        # 点击确定按钮
        logger.info("点击确定查询...")
        click_result = self._click_query_confirm_button()
        time.sleep(5)
        
        # 等待结果页
        result_frame = self._find_frame_containing(['#dlAll'], timeout=15)
        if not result_frame:
            # 检查是否无数据
            if not self._check_has_data():
                logger.info("查询结果为空")
                return self._create_no_data_marker(start_date, end_date)
            raise Exception("查询结果页加载失败")
        
        self._work_frame = result_frame
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
