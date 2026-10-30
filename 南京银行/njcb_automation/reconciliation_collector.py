# -*- coding: utf-8 -*-
"""
电子对账单采集模块 - 南京银行适配版
页面路径：账户管理 -> 电子对账单查询
下载格式：CSV
"""
import sys
import time
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from playwright.sync_api import Page
from logger import setup_logger
from config_loader import ReconciliationConfig
from common.download_helper import get_downloaded_files_before, wait_and_find_new_file

logger = setup_logger()


class ReconciliationCollector:
    """电子对账单采集器 - 南京银行适配版"""

    def __init__(self, page: Page, config: ReconciliationConfig, statement_dates: Optional[dict] = None):
        self.page = page
        self.config = config
        self.download_path = Path(config.download_path)
        self.download_path.mkdir(parents=True, exist_ok=True)

        # 设置日期范围（优先使用传入的日期）
        if statement_dates:
            self.start_date = statement_dates.get('start_date', '')
            self.end_date = statement_dates.get('end_date', '')
        else:
            self.start_date = ''
            self.end_date = ''

        if not self.start_date or not self.end_date:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=config.days_back)
            self.start_date = start_date.strftime("%Y-%m-%d")
            self.end_date = end_date.strftime("%Y-%m-%d")

        # 设置归档路径（按年月）
        self.current_date = datetime.now()
        if config.archive_by_month:
            self.archive_path = self.download_path / f"{self.current_date.year}" / f"{self.current_date.month:02d}"
            self.archive_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"电子对账单归档路径: {self.archive_path}")
            self._cleanup_old_archives()
        else:
            self.archive_path = self.download_path

        # 支持打包环境
        if getattr(sys, 'frozen', False):
            self.browser_download_path = Path(sys.executable).parent / "downloads"
        else:
            self.browser_download_path = Path(__file__).parent / "downloads"
        self.browser_download_path.mkdir(parents=True, exist_ok=True)

        # 南京银行特有：自己读取 config.ini 中的 company_name（与电子回单一致）
        self.company_name = self._load_company_name()

    def _load_company_name(self) -> str:
        """从 config.ini 读取单位名称"""
        try:
            import configparser
            cp = configparser.ConfigParser()
            for ini_path in [Path("config.ini"), Path(__file__).parent / "config.ini"]:
                if ini_path.exists():
                    cp.read(ini_path, encoding='utf-8-sig')
                    break
            return cp.get('collection.statements', 'company_name', fallback='')
        except Exception:
            return ''

    # ==================== 公共辅助方法 ====================

    def _cleanup_old_archives(self):
        """清理超过保留期限的归档目录"""
        retention_months = self.config.retention_months
        if retention_months <= 0:
            return

        cutoff_date = self.current_date.replace(day=1)
        for _ in range(retention_months):
            if cutoff_date.month == 1:
                cutoff_date = cutoff_date.replace(year=cutoff_date.year - 1, month=12)
            else:
                cutoff_date = cutoff_date.replace(month=cutoff_date.month - 1)

        logger.info(f"清理 {retention_months} 个月前的电子对账单归档数据，截止日期: {cutoff_date.strftime('%Y-%m')}")

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

    def _get_downloaded_files_before(self) -> dict:
        """获取多个下载目录中的文件列表"""
        return get_downloaded_files_before(self.browser_download_path)

    def _wait_and_find_new_file(self, before_files: dict, timeout: int = 60) -> Optional[Path]:
        """等待并查找新下载的文件"""
        return wait_and_find_new_file(before_files, timeout)

    def _move_to_download_path(self, source_file: Path, account: str) -> str:
        """将文件移动到归档目录，按指定格式重命名（不带账号后缀，重名直接覆盖）"""
        timestamp = datetime.now().strftime("%Y%m%d")
        ext = source_file.suffix or ".xlsx"
        # 下载临时文件按配置格式修正后缀
        if ext.lower() in ('.tmp', '.crdownload', '.partial'):
            ext = ".xlsx"
        file_name = f"南京银行_电子对账单_{timestamp}{ext}"
        file_path = self.archive_path / file_name

        # 重名直接覆盖
        if file_path.exists():
            file_path.unlink()

        shutil.move(str(source_file), str(file_path))
        logger.info(f"电子对账单文件已保存: {file_path}")
        return str(file_path)

    def _safe_filename(self, text: str) -> str:
        """生成安全的文件名片段"""
        keep_chars = (' ', '.', '_', '-')
        result = ""
        for c in text:
            if c.isalnum() or c in keep_chars:
                result += c
            else:
                result += "_"
        return result.strip().replace(" ", "_")[:50]

    def _create_no_data_marker(self, start_date: str, end_date: str, account: str) -> str:
        """创建无数据标记文件"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"南京银行_电子对账单_无数据_{timestamp}.txt"
        file_path = self.archive_path / file_name

        content = f"""南京银行企业网银 - 电子对账单采集结果
========================================
查询时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
查询日期范围: {start_date} 至 {end_date}
查询账户: {account if account else '默认账户'}

查询结果: 无数据
说明: 在指定的日期范围内未查询到任何电子对账单。

可能的原因:
1. 该日期范围内确实没有生成对账单
2. 选择的账户在该时间段内无对账单
3. 查询条件有误（如日期格式、账户选择等）

建议:
- 检查日期范围是否正确
- 确认账户是否有对账单
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

    # ==================== 页面判断 ====================

    def _is_reconciliation_page(self) -> bool:
        """检查当前是否在电子对账单查询页面"""
        indicators = [
            "text=电子对账单",
            "text=本方账号",
            "text=全部结果下载",
            "text=文件中转站",
            "text=勾选结果下载",
        ]
        for indicator in indicators:
            try:
                if self.page.locator(indicator).first.is_visible(timeout=2000):
                    return True
            except Exception:
                continue
        return False

    def _check_has_data(self) -> bool:
        """检查查询结果是否有数据"""
        no_data_indicators = [
            "text=暂无查询结果",
            "text=无数据显示",
            "text=暂无数据",
            "text=未查询到",
            "text=暂无符合条件的查询结果",
            "text=没有数据",
            ".no-data",
            ".empty",
            ".ant-empty",
            ".el-empty",
        ]
        for indicator in no_data_indicators:
            try:
                if self.page.locator(indicator).first.is_visible(timeout=2000):
                    return False
            except Exception:
                continue
        return True

    # ==================== 核心采集流程 ====================

    def collect(self, accounts: Optional[List[str]] = None) -> List[str]:
        """采集电子对账单"""
        downloaded_files = []
        target_accounts = accounts or self.config.accounts

        start_date_str = self.start_date
        end_date_str = self.end_date

        logger.info(f"开始采集电子对账单，日期范围: {start_date_str} 至 {end_date_str}")

        try:
            # 导航到电子对账单查询页面
            self._navigate_to_reconciliation_page()

            # 先选择单位（电子对账单页面单位是必填项）
            self._select_company()

            if not target_accounts:
                target_accounts = self._get_all_accounts()

            for account in target_accounts:
                try:
                    file_path = self._download_reconciliation(account, start_date_str, end_date_str)
                    if file_path:
                        downloaded_files.append(file_path)
                except Exception as e:
                    logger.error(f"采集账户 {account} 电子对账单失败: {e}")
                    continue

        except Exception as e:
            logger.error(f"采集电子对账单过程出错: {e}")

        logger.info(f"电子对账单采集完成，共下载 {len(downloaded_files)} 个文件")
        return downloaded_files

    def _navigate_to_reconciliation_page(self):
        """导航到电子对账单查询页面"""
        logger.info("导航到电子对账单查询页面...")
        reconciliation_url = "https://ntms.njcb.com.cn/pay/bankReconciliation"
        logger.info(f"直接访问电子对账单URL: {reconciliation_url}")
        self.page.goto(reconciliation_url, wait_until="networkidle", timeout=60000)
        logger.info("网络空闲，页面基础加载完成")

        # 等待关键元素渲染
        key_elements = [
            "text=本方账号",
            "text=全部结果下载",
            "button:has-text('查询')",
            "text=电子对账单",
        ]
        logger.info("等待页面渲染查询表单...")
        for attempt in range(30):
            for selector in key_elements:
                try:
                    if self.page.locator(selector).first.is_visible(timeout=1000):
                        logger.info(f"页面已渲染，检测到元素: {selector}")
                        time.sleep(2)
                        break
                except Exception:
                    continue
            else:
                time.sleep(1)
                continue
            break

        # 关闭可能展开的遮挡面板
        logger.info("尝试关闭可能展开的遮挡面板...")
        try:
            self.page.evaluate("""
                () => {
                    document.querySelectorAll('.menu-popup, .ant-drawer, .ant-modal-mask, .v-modal').forEach(el => el.remove());
                    return true;
                }
            """)
            time.sleep(0.5)
        except Exception:
            pass

        try:
            self.page.keyboard.press("Escape")
            time.sleep(0.5)
        except Exception:
            pass

        logger.info("页面准备就绪")

    # ==================== 选择单位 ====================

    def _select_company(self):
        """选择单位（select-tree-wrap 自定义树形选择器，与电子回单同一套逻辑）"""
        company_name = self.company_name

        try:
            company_select = None

            # 方法1：通过label找到 select-tree-wrap
            try:
                label = self.page.locator("label:has-text('单位')").first
                if label.count() > 0:
                    parent = label.locator("xpath=../..")
                    tree_wrap = parent.locator(".select-tree-wrap").first
                    if tree_wrap.count() > 0:
                        company_select = tree_wrap
            except Exception:
                pass

            # 方法2：直接找 select-tree-wrap
            if not company_select:
                try:
                    tree_wrap = self.page.locator(".select-tree-wrap").first
                    if tree_wrap.count() > 0:
                        company_select = tree_wrap
                except Exception:
                    pass

            if not company_select:
                logger.info("未找到单位选择框，可能已预选或不需要选择")
                return

            company_select.click(force=True)
            logger.info("已点击单位选择框，等待选项加载...")
            time.sleep(2)

            selected = False

            if company_name:
                for keyword in [company_name[:4], company_name[:6], "上海", "交通"]:
                    if len(keyword) < 2:
                        continue
                    try:
                        opt = self.page.locator(f"text={keyword}").first
                        if opt.count() > 0 and opt.is_visible(timeout=1000):
                            opt.click(force=True)
                            logger.info(f"已选择单位（模糊匹配）: {keyword}")
                            selected = True
                            break
                    except Exception:
                        continue

            if not selected:
                option_selectors = [
                    "li",
                    "[role='treeitem']",
                    "[role='option']",
                    ".ant-select-dropdown-menu-item",
                    ".ant-select-tree-node-content-wrapper",
                    ".tree-node",
                ]
                for sel in option_selectors:
                    try:
                        options = self.page.locator(sel).all()
                        if options:
                            texts = [o.text_content().strip() for o in options if o.text_content() and o.text_content().strip()]
                            if texts:
                                logger.info(f"找到选项 ({sel}): {texts[:5]}")
                                options[0].click(force=True)
                                logger.info(f"已选择第一个单位: {texts[0]}")
                                selected = True
                                break
                    except Exception:
                        continue

            if selected:
                time.sleep(2)
            else:
                logger.warning("未找到单位选项，跳过")
                try:
                    self.page.keyboard.press("Escape")
                except Exception:
                    pass

        except Exception as e:
            logger.warning(f"选择单位失败: {e}，继续执行")

    # ==================== 获取/选择账户 ====================

    def _get_all_accounts(self) -> List[str]:
        """获取所有可用账户"""
        logger.info("正在获取账户列表...")
        accounts = []

        try:
            dropdown = self._find_account_dropdown()
            if not dropdown:
                logger.info("未找到本方账号下拉框")
                return [""]

            # 检查是否是自定义表格选择器 select-table-wrap
            is_table_wrap = False
            try:
                cls = dropdown.get_attribute("class") or ""
                is_table_wrap = "select-table-wrap" in cls
                if not is_table_wrap:
                    parent = dropdown.locator("xpath=ancestor::div[contains(@class,'select-table-wrap')]").first
                    if parent.count() > 0:
                        is_table_wrap = True
                        dropdown = parent
            except Exception:
                pass

            if is_table_wrap:
                return self._get_all_accounts_from_table_wrap(dropdown)

            # 标准 Ant Design select
            is_disabled = False
            try:
                is_disabled = dropdown.locator(".ant-select-disabled").count() > 0 or "disabled" in (dropdown.get_attribute("class") or "")
            except Exception:
                pass

            if is_disabled:
                logger.info("本方账号下拉框处于disabled状态，等待联动生效...")
                time.sleep(3)
                dropdown = self._find_account_dropdown()
                if not dropdown:
                    logger.info("重新查找后仍未找到本方账号下拉框")
                    return [""]

            dropdown.click(force=True)
            time.sleep(1.5)

            option_selectors = [
                ".ant-select-dropdown-menu-item",
                ".ant-select-tree-node-content-wrapper",
                "li",
                "[role='option']",
            ]
            for sel in option_selectors:
                try:
                    options = self.page.locator(sel).all()
                    for option in options:
                        text = option.text_content()
                        if text and text.strip():
                            accounts.append(text.strip())
                    if accounts:
                        break
                except Exception:
                    continue

            # 关闭下拉框
            try:
                self.page.keyboard.press("Escape")
                time.sleep(0.3)
            except Exception:
                pass

        except Exception as e:
            logger.debug(f"获取账户列表失败: {e}")

        if not accounts:
            logger.info("未获取到账户列表，将使用默认账户")
            return [""]

        logger.info(f"获取到 {len(accounts)} 个账户: {accounts}")
        return accounts

    def _get_all_accounts_from_table_wrap(self, dropdown) -> List[str]:
        """从 select-table-wrap 组件中获取账号列表"""
        accounts = []
        try:
            table_options = dropdown.locator(".table-options").first
            if not (table_options.count() > 0 and table_options.is_visible(timeout=500)):
                input_el = dropdown.locator("input.select-table-input").first
                if input_el.count() > 0:
                    input_el.click(force=True)
                else:
                    dropdown.click(force=True)
                time.sleep(1.5)

            try:
                table_options.wait_for(timeout=5000)
            except Exception:
                logger.warning("等待 table-options 超时")
                return [""]

            rows = []
            for _ in range(10):
                rows = dropdown.locator(".table-options tbody tr").all()
                if rows:
                    break
                time.sleep(0.5)

            for row in rows:
                tds = row.locator("td").all()
                if tds:
                    account_text = tds[0].text_content().strip()
                    if account_text:
                        accounts.append(account_text)

            # 关闭下拉框
            try:
                self.page.keyboard.press("Escape")
                time.sleep(0.3)
            except Exception:
                pass

        except Exception as e:
            logger.debug(f"从表格选择器获取账户列表失败: {e}")

        if not accounts:
            logger.info("未从表格选择器获取到账户列表，将使用默认账户")
            return [""]

        logger.info(f"从表格选择器获取到 {len(accounts)} 个账户: {accounts}")
        return accounts

    def _find_account_dropdown(self):
        """查找本方账号下拉框元素"""
        selectors = [
            ".ant-form-item:has-text('本方账号') .select-table-wrap",
            ".select-table-wrap-biz",
            ".ant-form-item:has-text('本方账号') .ant-select",
            "//div[contains(@class,'ant-form-item')][.//label[contains(text(),'本方账号')]]//div[contains(@class,'ant-select')]",
            "text=本方账号 >> xpath=../following-sibling::div//div[contains(@class,'ant-select')]",
            ".ant-form-item:has-text('本方账号') input",
            "input[placeholder*='账号']",
            "input[placeholder*='请选择']",
        ]
        for sel in selectors:
            try:
                el = self.page.locator(sel).first
                if el.count() > 0:
                    return el
            except Exception:
                continue
        return None

    def _select_account(self, account: str) -> bool:
        """选择本方账号"""
        if not account:
            logger.info("未指定账号，使用默认")
            return True

        try:
            dropdown = self._find_account_dropdown()
            if not dropdown:
                logger.warning("未找到本方账号下拉框")
                return False

            # 检查是否是自定义表格选择器
            is_table_wrap = False
            try:
                cls = dropdown.get_attribute("class") or ""
                is_table_wrap = "select-table-wrap" in cls
                if not is_table_wrap:
                    parent = dropdown.locator("xpath=ancestor::div[contains(@class,'select-table-wrap')]").first
                    if parent.count() > 0:
                        is_table_wrap = True
                        dropdown = parent
            except Exception:
                pass

            if is_table_wrap:
                return self._select_account_in_table_wrap(dropdown, account)

            # 标准 Ant Design select
            dropdown.click()
            time.sleep(1)

            self.page.wait_for_selector(".ant-select-dropdown-menu-item", timeout=5000)
            time.sleep(0.5)

            options = self.page.locator(".ant-select-dropdown-menu-item").all()
            for option in options:
                text = option.text_content().strip() if option.text_content() else ""
                if account in text or text in account:
                    option.click(force=True)
                    logger.info(f"已选择本方账号: {text}")
                    time.sleep(1)
                    return True

            if options:
                first_text = options[0].text_content().strip() if options[0].text_content() else ""
                options[0].click(force=True)
                logger.info(f"未找到匹配账号 {account}，选择第一个: {first_text}")
                time.sleep(1)
                return True

            return False

        except Exception as e:
            logger.error(f"选择本方账号失败: {e}")
            return False

    def _select_account_in_table_wrap(self, dropdown, account: str) -> bool:
        """在 select-table-wrap 组件中选择账号"""
        try:
            table_options = dropdown.locator(".table-options").first
            if not (table_options.count() > 0 and table_options.is_visible(timeout=500)):
                input_el = dropdown.locator("input.select-table-input").first
                if input_el.count() > 0:
                    input_el.click(force=True)
                else:
                    dropdown.click(force=True)
                time.sleep(1)

            dropdown.locator(".table-options tbody tr").first.wait_for(timeout=5000)
            time.sleep(0.5)

            rows = dropdown.locator(".table-options tbody tr").all()
            for row in rows:
                tds = row.locator("td").all()
                if not tds:
                    continue
                text = tds[0].text_content().strip() if tds[0].text_content() else ""
                if account in text or text in account:
                    tds[0].click(force=True)
                    logger.info(f"已选择本方账号: {text}")
                    time.sleep(0.5)
                    try:
                        self.page.keyboard.press("Escape")
                        time.sleep(0.3)
                    except Exception:
                        pass
                    return True

            if rows:
                first_tds = rows[0].locator("td").all()
                first_text = first_tds[0].text_content().strip() if first_tds else ""
                if first_tds:
                    first_tds[0].click(force=True)
                else:
                    rows[0].click(force=True)
                logger.info(f"未找到匹配账号 {account}，选择第一个: {first_text}")
                time.sleep(0.5)
                try:
                    self.page.keyboard.press("Escape")
                    time.sleep(0.3)
                except Exception:
                    pass
                return True

            return False

        except Exception as e:
            logger.error(f"在表格选择器中选择账号失败: {e}")
            return False

    # ==================== 日期设置 ====================

    # JS 检测脚本：判断"按日期"radio 是否已选中
    _JS_IS_DATE_MODE = """
        () => {
            var wrappers = document.querySelectorAll(
                '.ant-radio-wrapper-checked, [class*="radio"][class*="checked"]'
            );
            for (var i = 0; i < wrappers.length; i++) {
                if ((wrappers[i].textContent || '').trim() === '按日期') return true;
            }
            return false;
        }
    """

    # JS 点击脚本：精确点击文本为"按日期"的单选项（避免误匹配组标题"按月查询/按日期："）
    _JS_CLICK_DATE_RADIO = """
        () => {
            var all = document.querySelectorAll('span, label, div, a, button');
            for (var i = 0; i < all.length; i++) {
                var el = all[i];
                if ((el.textContent || '').trim() !== '按日期') continue;
                if (!el.offsetParent) continue;
                // 只处理叶子或近似叶子节点，跳过包含"按月查询"的组标题
                if ((el.textContent || '').indexOf('按月查询') >= 0) continue;
                var wrapper = el.closest('.ant-radio-wrapper, .el-radio, label, [class*="radio-item"]') || el;
                wrapper.click();
                var input = null;
                if (wrapper.querySelector) input = wrapper.querySelector('input[type="radio"]');
                if (!input) {
                    var w2 = el.closest('[class*="radio"]');
                    if (w2 && w2.querySelector) input = w2.querySelector('input[type="radio"]');
                }
                if (input && !input.checked) input.click();
                return {clicked: true};
            }
            return {clicked: false};
        }
    """

    def _ensure_date_mode(self):
        """确保页面处于按日期查询模式"""
        try:
            # 先关闭可能打开的弹层（如按月模式下的月份选择面板）
            try:
                self.page.keyboard.press("Escape")
                time.sleep(0.5)
            except Exception:
                pass

            # 检查当前是否已选中"按日期"
            try:
                if self.page.evaluate(self._JS_IS_DATE_MODE):
                    logger.info("当前已处于按日期查询模式")
                    return
            except Exception:
                pass

            # JS 精确点击"按日期"radio，点击后校验，最多重试3次
            for attempt in range(3):
                js_result = self.page.evaluate(self._JS_CLICK_DATE_RADIO)
                if not (js_result and js_result.get('clicked')):
                    logger.info("未找到'按日期'单选项，使用页面默认模式")
                    return

                logger.info(f"已点击'按日期'（第 {attempt + 1} 次），等待页面切换...")
                time.sleep(2)

                try:
                    if self.page.evaluate(self._JS_IS_DATE_MODE):
                        logger.info("已切换到按日期查询模式")
                        return
                except Exception:
                    pass
                logger.debug("点击后校验未通过，重试...")

            logger.warning("多次点击'按日期'后仍未生效，继续执行")
        except Exception as e:
            logger.warning(f"确保按日期模式失败: {e}")

    def _set_date_range(self, start_date: str, end_date: str):
        """设置日期范围"""
        logger.info(f"设置日期范围: {start_date} 至 {end_date}")

        # 先确保处于按日期模式
        self._ensure_date_mode()

        # 尝试精确匹配的快捷按钮（近1周/近1月/近3月/近半年）
        if self._try_quick_date_select(start_date, end_date):
            return

        # 通过日期选择器设置
        try:
            if self._set_date_via_picker(start_date, end_date):
                return
        except Exception as e:
            logger.debug(f"日期选择器设置失败: {e}")

        # 兜底：使用近1月/近3月快捷按钮（电子对账单页面常见）
        if self._try_fallback_quick_buttons():
            return

        logger.warning("日期设置均失败，将使用页面默认日期范围继续执行")

    def _try_fallback_quick_buttons(self) -> bool:
        """兜底：点击任意可用的快捷日期按钮"""
        labels = ["近1月", "近3月", "1月", "近1周", "近半年"]
        for label in labels:
            try:
                # 优先匹配完全相等或包含关系的按钮
                selectors = [
                    f"button:has-text('{label}')",
                    f".ant-btn:has-text('{label}')",
                    f"span:has-text('{label}')",
                    f"text={label}",
                ]
                for sel in selectors:
                    btn = self.page.locator(sel).first
                    if btn.count() > 0 and btn.is_visible(timeout=1000):
                        btn.click(force=True)
                        logger.info(f"兜底使用快捷日期: {label}")
                        time.sleep(1)
                        return True
            except Exception:
                continue
        return False

    def _try_quick_date_select(self, start_date: str, end_date: str) -> bool:
        """尝试使用快捷日期按钮"""
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")
            delta = (end - start).days + 1

            quick_map = {
                7: "近1周",
                30: "近1月",
                90: "近3月",
                180: "近半年",
            }

            for days, label in quick_map.items():
                if abs(delta - days) <= 3:
                    btn = self.page.locator(f"text={label}").first
                    if btn.count() > 0 and btn.is_visible(timeout=2000):
                        btn.click()
                        logger.info(f"使用快捷日期: {label}")
                        time.sleep(1)
                        return True

            return False
        except Exception:
            return False

    def _set_date_via_picker(self, start_date: str, end_date: str) -> bool:
        """通过日期选择器设置日期"""
        try:
            # 电子对账单页面的日期输入在"交易时间"表单项内，限定范围避免误点单位/账户类别下拉
            container = None
            container_selectors = [
                "xpath=//div[contains(@class,'ant-form-item')][.//label[contains(text(),'交易时间')]]",
                "xpath=//div[contains(@class,'form-item')][.//*[contains(text(),'交易时间')]]",
            ]
            for sel in container_selectors:
                try:
                    el = self.page.locator(sel).first
                    if el.count() > 0:
                        container = el
                        logger.info(f"找到交易时间表单项: {sel}")
                        break
                except Exception:
                    continue

            scope = container if container else self.page
            picker_selectors = [
                ".ant-calendar-picker",
                ".ant-picker",
                ".ant-picker-range",
                "input[placeholder*='开始时间']",
                "input[placeholder*='结束时间']",
                "input[placeholder*='开始日期']",
                "input[placeholder*='结束日期']",
                "input[placeholder*='日期范围']",
                "input[placeholder*='请选择']",
                "input",
            ]

            picker = None
            for sel in picker_selectors:
                try:
                    el = scope.locator(sel).first
                    if el.count() > 0 and el.is_visible(timeout=2000):
                        picker = el
                        logger.info(f"找到日期选择器: {sel}")
                        break
                except Exception:
                    continue

            if not picker:
                logger.warning("未找到日期选择器")
                return False

            try:
                picker.click(timeout=5000)
            except Exception:
                picker.click(force=True, timeout=5000)
            time.sleep(1)

            # 确保目标月份在日历面板中可见（避免因默认显示当月/次月而点不到上月日期）
            self._ensure_calendar_month(min(start_date, end_date))

            if self._click_date_cell(start_date) and self._click_date_cell(end_date):
                time.sleep(0.5)
                self.page.keyboard.press("Enter")
                time.sleep(1)
                if self._verify_date_input(start_date, end_date):
                    logger.info("通过点击日历单元格设置日期成功")
                    return True
                logger.warning("点击日历单元格后输入框值与预期不符，尝试 JS 兜底")

            logger.info("尝试 JS 直接设置日期输入框")
            js_success = self.page.evaluate("""(args) => {
                function setNativeValue(input, value) {
                    try {
                        input.removeAttribute('readonly');
                        input.removeAttribute('disabled');
                    } catch(e) {}
                    try {
                        var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                        setter.call(input, value);
                    } catch(e) {
                        input.value = value;
                    }
                    ['focus', 'input', 'change', 'blur'].forEach(function(type) {
                        input.dispatchEvent(new Event(type, {bubbles: true}));
                    });
                }
                var inputs = document.querySelectorAll(
                    '.ant-calendar-range-picker-input, .ant-calendar-input, '
                    + '.ant-picker-input input, input[placeholder*="开始时间"], input[placeholder*="结束时间"], '
                    + 'input[placeholder*="开始日期"], input[placeholder*="结束日期"]'
                );
                if (inputs.length >= 2) {
                    setNativeValue(inputs[0], args.start_date);
                    setNativeValue(inputs[1], args.end_date);
                    return true;
                }
                return false;
            }""", {"start_date": start_date, "end_date": end_date})

            if js_success:
                time.sleep(0.5)
                self.page.keyboard.press("Enter")
                time.sleep(1)
                if self._verify_date_input(start_date, end_date):
                    logger.info("通过 JS 设置日期成功")
                    return True
                logger.warning("JS 设置后输入框值仍与预期不符")

            logger.warning("未找到可用于设置日期的元素")
            return False

        except Exception as e:
            logger.error(f"日历选择日期失败: {e}")
            return False

    def _verify_date_input(self, start_date: str, end_date: str) -> bool:
        """读取日期输入框实际值并校验"""
        try:
            inputs = self.page.locator(
                '.ant-calendar-range-picker-input, .ant-calendar-input, '
                '.ant-picker-input input, input[placeholder*="开始时间"], input[placeholder*="结束时间"], '
                'input[placeholder*="开始日期"], input[placeholder*="结束日期"]'
            ).all()
            values = [inp.input_value() for inp in inputs if inp.input_value()]
            logger.info(f"日期输入框当前值: {values}")
            start_ok = any(start_date in v for v in values)
            end_ok = any(end_date in v for v in values)
            return start_ok and end_ok
        except Exception as e:
            logger.debug(f"验证日期输入失败: {e}")
            return False

    def _click_date_cell(self, date_str: str) -> bool:
        """在已打开的日历弹窗中点击指定日期"""
        try:
            # 先规范化为 YYYY-MM-DD，避免 2026-8-27 因缺前导零导致 title 匹配失败
            original = date_str
            try:
                date_str = datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m-%d")
                if date_str != original:
                    logger.info(f"日期格式规范化: {original} -> {date_str}")
            except Exception:
                pass

            result = self.page.evaluate("""(dateStr) => {
                var selectors = [
                    '.ant-calendar-cell[title]',
                    '.ant-calendar-date[title]',
                    '[class*="calendar-cell"][title]',
                    '[class*="calendar-date"][title]'
                ];
                var cells = [];
                selectors.forEach(function(sel) {
                    try { cells = cells.concat(Array.from(document.querySelectorAll(sel))); } catch(e) {}
                });
                for (var i = 0; i < cells.length; i++) {
                    var title = cells[i].getAttribute('title');
                    if (title) {
                        var normalized = title.replace(/\\//g, '-');
                        if (normalized === dateStr || normalized.startsWith(dateStr)) {
                            cells[i].click();
                            return {
                                clicked: true,
                                method: 'title',
                                title: title,
                                className: cells[i].getAttribute('class') || '',
                                text: (cells[i].textContent || '').trim()
                            };
                        }
                    }
                }

                // 兜底：根据面板年月 + 日期数字匹配，跳过上/下月的填充日期
                var parts = dateStr.split('-');
                var targetYear = parseInt(parts[0], 10);
                var targetMonth = parseInt(parts[1], 10);
                var targetDay = parseInt(parts[2], 10);
                var panels = document.querySelectorAll('.ant-calendar-range-part, .ant-calendar-panel, .ant-calendar-range-left, .ant-calendar-range-right');
                for (var p = 0; p < panels.length; p++) {
                    var panel = panels[p];
                    var header = panel.textContent || '';
                    var monthStr = targetMonth < 10 ? '0' + targetMonth : targetMonth;
                    if (header.includes(targetYear + '') && (header.includes(monthStr) || header.includes(targetMonth + '月'))) {
                        // 只取 td 单元格（ant-calendar-cell），通过父层 class 判断是否为上月/下月填充格
                        var cells = panel.querySelectorAll('.ant-calendar-cell');
                        for (var d = 0; d < cells.length; d++) {
                            var cls = cells[d].getAttribute('class') || '';
                            if (cls.includes('last-month-cell') || cls.includes('next-month-cell') || cls.includes('out-view')) {
                                continue;
                            }
                            var dateSpan = cells[d].querySelector('.ant-calendar-date');
                            var txt = dateSpan ? (dateSpan.textContent || '').trim() : (cells[d].textContent || '').trim();
                            if (txt === targetDay.toString()) {
                                cells[d].click();
                                return {
                                    clicked: true,
                                    method: 'panel',
                                    title: cells[d].getAttribute('title') || '',
                                    className: cls,
                                    text: txt
                                };
                            }
                        }
                    }
                }
                return {clicked: false, method: '', title: '', className: '', text: ''};
            }""", date_str)

            if result and result.get('clicked'):
                logger.info(
                    f"点击日期单元格: {date_str}, 方式: {result.get('method')}, "
                    f"title={result.get('title')}, class={result.get('className')}, text={result.get('text')}"
                )
                return True
            logger.warning(f"未找到可点击的日期单元格: {date_str}")
            return False
        except Exception as e:
            logger.debug(f"点击日期单元格失败: {e}")
            return False

    def _ensure_calendar_month(self, date_str: str) -> bool:
        """点击 prev/next 月份按钮，确保目标年月显示在日历面板中"""
        try:
            result = self.page.evaluate("""(dateStr) => {
                var parts = dateStr.split('-');
                var targetYear = parseInt(parts[0], 10);
                var targetMonth = parseInt(parts[1], 10);
                var targetVal = targetYear * 12 + (targetMonth - 1);

                function getDisplayedMonths() {
                    var months = [];
                    var panels = document.querySelectorAll(
                        '.ant-calendar-range-part, .ant-calendar-panel, .ant-calendar-range-left, .ant-calendar-range-right'
                    );
                    for (var p = 0; p < panels.length; p++) {
                        var header = panels[p].textContent || '';
                        var yearMatch = header.match(/(\\d{4})/);
                        var monthMatch = header.match(/(\\d{1,2})\\s*月/);
                        if (yearMatch && monthMatch) {
                            months.push({
                                year: parseInt(yearMatch[1], 10),
                                month: parseInt(monthMatch[1], 10)
                            });
                        }
                    }
                    return months;
                }

                for (var attempt = 0; attempt < 24; attempt++) {
                    var displayed = getDisplayedMonths();
                    var found = displayed.some(function(m) {
                        return m.year === targetYear && m.month === targetMonth;
                    });
                    if (found) {
                        return {success: true, displayed: displayed, attempts: attempt};
                    }
                    if (displayed.length === 0) {
                        return {success: false, reason: 'no_panels'};
                    }

                    var first = displayed[0];
                    var firstVal = first.year * 12 + (first.month - 1);
                    if (targetVal < firstVal) {
                        // 目标月份在左面板之前，点左侧面板的上月按钮
                        var prevBtns = document.querySelectorAll('.ant-calendar-prev-month-btn');
                        if (prevBtns.length > 0) {
                            prevBtns[0].click();
                        } else {
                            return {success: false, reason: 'no_prev_btn'};
                        }
                    } else {
                        // 目标月份在右面板之后，点右侧面板的下月按钮
                        var nextBtns = document.querySelectorAll('.ant-calendar-next-month-btn');
                        if (nextBtns.length > 0) {
                            nextBtns[nextBtns.length - 1].click();
                        } else {
                            return {success: false, reason: 'no_next_btn'};
                        }
                    }
                }
                return {success: false, reason: 'max_attempts'};
            }""", date_str)
            logger.info(f"日历月份导航结果: {result}")
            return result.get('success', False)
        except Exception as e:
            logger.warning(f"日历月份导航失败: {e}")
            return False

    # ==================== 查询与下载 ====================

    def _click_query_button(self) -> bool:
        """点击查询按钮"""
        selectors = [
            ".ant-query-btn",
            "button.ant-btn-primary:has-text('查')",
            "button:has-text('查询')",
            ".ant-btn:has-text('查询')",
            ".ant-btn-primary:has-text('查询')",
            "a:has-text('查询')",
            "div:has-text('查询')",
            "text=查询",
            "[type='button']:has-text('查询')",
        ]
        for selector in selectors:
            try:
                btn = self.page.locator(selector).first
                if btn.count() > 0 and btn.is_visible(timeout=2000):
                    btn.click(force=True)
                    logger.info(f"已点击查询按钮: {selector}")
                    time.sleep(3)
                    return True
            except Exception:
                continue
        logger.warning("未找到查询按钮")
        return False

    def _download_reconciliation(self, account: str, start_date: str, end_date: str) -> Optional[str]:
        """下载单个账户的电子对账单"""
        logger.info(f"正在处理账户 {account} 的电子对账单...")

        # 选择本方账号
        if not self._select_account(account):
            logger.warning("选择本方账号失败，将使用页面默认值")

        # 设置日期范围
        self._set_date_range(start_date, end_date)

        # 点击查询按钮
        if not self._click_query_button():
            return None

        # 检查是否有数据
        if not self._check_has_data():
            logger.info("当前查询结果为空")
            return self._create_no_data_marker(start_date, end_date, account)

        logger.info("检测到数据，开始下载...")

        # 记录下载前的文件列表
        before_files = self._get_downloaded_files_before()

        try:
            # 点击"全部结果下载"
            download_btn = self.page.locator("text=全部结果下载").first
            if download_btn.count() == 0 or not download_btn.is_visible(timeout=3000):
                logger.warning("未找到'全部结果下载'按钮")
                return None

            download_btn.click(force=True)
            logger.info("已点击'全部结果下载'")
            time.sleep(2)

            # 处理下载格式弹窗
            new_file = self._handle_download_dialog(before_files, timeout=60)

            if new_file:
                if new_file.suffix in ['.html', '.htm']:
                    logger.warning(f"下载的是HTML文件，可能还未生成完成")
                    new_file.unlink()
                    time.sleep(3)
                    return self._download_reconciliation(account, start_date, end_date)
                return self._move_to_download_path(new_file, account)

            logger.warning("下载失败：未找到下载文件")
            return None

        except Exception as e:
            logger.error(f"下载过程出错: {e}")
            return None

    def _handle_download_dialog(self, before_files: dict, timeout: int = 60) -> Optional[Path]:
        """处理下载格式选择弹窗并获取下载文件"""
        try:
            # 等待弹窗出现
            dialog = None
            for _ in range(10):
                for sel in [".ant-modal", ".ant-modal-content"]:
                    try:
                        d = self.page.locator(sel).first
                        if d.count() > 0 and d.is_visible(timeout=500):
                            dialog = d
                            break
                    except Exception:
                        continue
                if dialog:
                    break
                time.sleep(0.5)

            if dialog:
                logger.info("检测到下载格式弹窗")

                # 在弹窗内选择下载格式（默认 Excel）
                file_format = (self.config.file_format or 'excel').lower()
                format_candidates = {
                    'excel': ['Excel', 'EXCEL', 'excel'],
                    'csv': ['CSV', 'Csv', 'csv'],
                    'pdf': ['PDF', 'Pdf', 'pdf'],
                }
                candidates = format_candidates.get(file_format, format_candidates['excel'])

                format_selected = False
                for label_text in candidates:
                    try:
                        format_option = dialog.locator(f"text={label_text}").first
                        if format_option.count() > 0 and format_option.is_visible(timeout=2000):
                            try:
                                parent_label = format_option.locator("xpath=ancestor::label").first
                                if parent_label.count() > 0:
                                    radio = parent_label.locator("input[type='radio']")
                                    if radio.count() > 0 and radio.first.is_checked():
                                        logger.info(f"{label_text}格式已默认选中")
                                        format_selected = True
                                        break
                            except Exception:
                                pass
                            format_option.click(force=True)
                            logger.info(f"已选择{label_text}格式")
                            format_selected = True
                            break
                    except Exception:
                        continue

                if not format_selected:
                    # JS 兜底：点击文本匹配的单选项
                    try:
                        js_result = self.page.evaluate("""(fmt) => {
                            var all = document.querySelectorAll('label, span, div');
                            for (var i = 0; i < all.length; i++) {
                                var txt = (all[i].textContent || '').trim();
                                if (txt.toLowerCase() !== fmt) continue;
                                if (!all[i].offsetParent) continue;
                                var wrapper = all[i].closest('label') || all[i];
                                wrapper.click();
                                var input = wrapper.querySelector ? wrapper.querySelector('input[type="radio"]') : null;
                                if (input && !input.checked) input.click();
                                return true;
                            }
                            return false;
                        }""", file_format)
                        if js_result:
                            logger.info(f"通过 JS 选择{file_format}格式")
                            format_selected = True
                    except Exception:
                        pass

                if not format_selected:
                    logger.info("未找到格式选项，可能已默认选中")

                # 点击确定按钮
                confirm_selectors = [
                    ".ant-query-btn",
                    "button.ant-btn-primary:has-text('确')",
                    "button:has-text('确定'), .ant-btn-primary:has-text('确定')",
                    "button:has-text('确 定')",
                ]
                confirm_btn = None
                for sel in confirm_selectors:
                    try:
                        btn = dialog.locator(sel).first
                        if btn.count() > 0 and btn.is_visible(timeout=1000):
                            confirm_btn = btn
                            break
                    except Exception:
                        continue

                if confirm_btn:
                    try:
                        with self.page.expect_download(timeout=timeout * 1000) as download_info:
                            confirm_btn.click(force=True)
                            logger.info("已点击确定，等待下载...")
                        download = download_info.value
                        suggested = download.suggested_filename or f"download_{int(time.time())}"
                        download_path = self.browser_download_path / suggested
                        try:
                            download.save_as(str(download_path))
                            logger.info(f"下载完成: {download_path}")
                            return download_path
                        except Exception as save_err:
                            logger.warning(f"save_as 失败: {save_err}")
                            download_path = Path(download.path())
                            if download_path.exists():
                                logger.info(f"通过 path() 获取下载: {download_path}")
                                return download_path
                    except Exception as e:
                        logger.warning(f"expect_download捕获失败: {e}")
                        confirm_btn.click(force=True)
                        logger.info("已点击确定（回退模式）")
                else:
                    logger.warning("未找到弹窗内的确定按钮")
            else:
                logger.info("未检测到弹窗，可能直接下载")

            # 文件轮询等待下载完成
            time.sleep(3)
            return self._wait_and_find_new_file(before_files, timeout=timeout)

        except Exception as e:
            logger.error(f"处理下载弹窗失败: {e}")
            time.sleep(3)
            return self._wait_and_find_new_file(before_files, timeout=timeout)
