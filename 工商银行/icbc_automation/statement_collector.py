# -*- coding: utf-8 -*-
"""
银行流水采集模块 - 工商银行适配（架子）
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

# 工行登录后通过“账户管理-明细查询”菜单进入，不直连 URL
STATEMENT_URL = ""


class StatementCollector:
    """银行流水采集器 - 工商银行适配"""

    def __init__(self, page: Page, config: StatementsConfig):
        self.page = page
        self.root = page
        self.config = config
        self.download_path = Path(config.download_path)
        self.download_path.mkdir(parents=True, exist_ok=True)

        self.current_date = datetime.now()
        if config.archive_by_month:
            self.archive_path = self.download_path / f"{self.current_date.year}" / f"{self.current_date.month:02d}"
            self.archive_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"流水归档路径: {self.archive_path}")
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
        retention_months = self.config.retention_months
        if retention_months <= 0:
            return

        cutoff_date = self.current_date.replace(day=1)
        for _ in range(retention_months):
            if cutoff_date.month == 1:
                cutoff_date = cutoff_date.replace(year=cutoff_date.year - 1, month=12)
            else:
                cutoff_date = cutoff_date.replace(month=cutoff_date.month - 1)

        logger.info(f"清理 {retention_months} 个月前的流水归档数据，截止日期: {cutoff_date.strftime('%Y-%m')}")

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
        """导航到交易明细查询页面（账户管理 → 明细查询）"""
        logger.info("导航到交易明细查询页面...")
        time.sleep(2)
        self._dismiss_ad()

        if self._click_menu(["text=账户管理"]):
            logger.info("已展开「账户管理」导航")
            time.sleep(1.5)

        if self._click_menu([
            "text=明细查询",
            "span.menu_item_title:text-is('明细查询')",
            "a.menu_item:has-text('明细查询')",
        ]):
            logger.info("已点击明细查询菜单，等待页面加载...")
            time.sleep(5)
            self._adopt_new_page()
            self.root = self._find_content_root()

            start = time.time()
            while time.time() - start < 30:
                self._adopt_new_page()
                self.root = self._find_content_root()
                if self._is_statement_page():
                    logger.info("已进入交易明细查询页面")
                    self._close_sidebar_menu()
                    return
                time.sleep(2)

            logger.warning("已点击菜单，但未识别到明细查询页面元素")

        if STATEMENT_URL:
            logger.info(f"尝试直接访问流水查询URL: {STATEMENT_URL}")
            try:
                self.page.goto(STATEMENT_URL, wait_until="domcontentloaded", timeout=30000)
                time.sleep(5)
                self.root = self._find_content_root()
                if self._is_statement_page():
                    return
            except Exception as e:
                logger.error(f"URL导航失败: {e}")

        raise Exception("未能进入交易明细查询页面")



    def _dismiss_ad(self):
        """关闭首页广告遮罩（#msgFrame），否则会挡住菜单点击。"""
        try:
            for frame in self.page.frames:
                if "newEiopAlertAd" in frame.url:
                    try:
                        checkbox = frame.locator(".PopPageFooter img").first
                        if checkbox.count() > 0:
                            checkbox.click(timeout=1000, force=True)
                    except Exception:
                        pass
                    break
        except Exception:
            pass
        try:
            self.page.evaluate(
                """() => {
                    const f = document.getElementById('msgFrame');
                    if (f) { f.style.display = 'none'; f.src = 'about:blank'; }
                    document.querySelectorAll('.PopPage, .PopPageMask, .PopPageBg').forEach(e => e.remove());
                }"""
            )
        except Exception:
            pass

    def _click_menu(self, texts: List[str]) -> bool:
        """按选择器点击菜单。工行左侧菜单项带 hide 类，Playwright 判定不可见，
        但仍能被 JS 触发，所以可见时正常点击，否则退化成 JS 点击。"""
        for frame in self.page.frames:
            for text in texts:
                try:
                    if text.startswith("text="):
                        keyword = text[5:]
                        loc = frame.get_by_text(keyword, exact=True).first
                        if loc.count() == 0:
                            loc = frame.get_by_text(keyword, exact=False).first
                    else:
                        loc = frame.locator(text).first
                    if loc.count() == 0:
                        continue
                    try:
                        visible = loc.is_visible()
                    except Exception:
                        visible = False
                    if visible:
                        try:
                            loc.click(timeout=3000)
                            logger.info(f"已点击菜单: {text}")
                            return True
                        except Exception:
                            pass
                    loc.evaluate("el => el.click()")
                    logger.info(f"已点击菜单(JS): {text}")
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
                if frame.locator(
                    "input[id*='beginDate'], input[id*='endDate'],"
                    " input[name*='date'], input[name*='Date'], #qryBtn"
                ).count() > 0:
                    logger.info(f"找到业务 iframe: {frame.url}")
                    return frame
            except Exception:
                continue
        return self.page



    def _close_sidebar_menu(self):
        """关闭侧边栏 / 弹窗"""
        try:
            self.page.keyboard.press("Escape")
            time.sleep(0.5)
            self.page.mouse.click(600, 400)
            time.sleep(0.5)
            logger.info("已关闭侧边栏菜单")
        except Exception as e:
            logger.debug(f"关闭侧边栏菜单失败: {e}")

    def _is_statement_page(self) -> bool:
        """检查当前是否在交易明细查询页面（以日期控件和查询按钮为准）"""
        for indicator in ("input#datepicker1-beginDate", "input[id*='beginDate']", "#qryBtn"):
            try:
                if self.root.locator(indicator).count() > 0:
                    return True
            except Exception:
                continue
        return False



    def _get_all_accounts(self) -> List[str]:
        """获取所有可用账户"""
        logger.info("正在获取账户列表...")
        accounts = []

        try:
            dropdown_selectors = [
                ".account-select",
                "select[name='account']",
                "text=请选择账户",
            ]

            for selector in dropdown_selectors:
                try:
                    dropdown = self.root.locator(selector).first
                    if dropdown.is_visible(timeout=3000):
                        dropdown.click()
                        time.sleep(1)
                        options = self.root.locator(".el-select-dropdown__item, option, .dropdown-item").all()
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
        """创建无数据标记文件"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"工商银行_银行流水_无数据_{timestamp}.txt"
        file_path = self.archive_path / file_name

        content = f"""工商银行企业网银 - 银行流水采集结果
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
        # TODO: 按工行实际空数据提示调整
        no_data_indicators = [
            "text=无数据显示",
            "text=暂无数据",
            "text=未查询到",
            "text=暂无符合条件的查询结果",
            ".no-data",
            ".empty",
        ]
        try:
            for indicator in no_data_indicators:
                try:
                    if self.root.locator(indicator).first.is_visible(timeout=2000):
                        return False
                except Exception:
                    continue
            return True
        except Exception:
            return True

    def _get_downloaded_files_before(self) -> dict:
        """获取下载目录中的初始文件列表"""
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
        """检查文件名是否是 GUID 格式（Chrome 临时下载文件）"""
        import re
        guid_pattern = r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
        return bool(re.match(guid_pattern, filename))

    def _wait_and_find_new_file(self, before_files: dict, timeout: int = 60) -> Optional[Path]:
        """等待并查找新下载的文件"""
        start_time = time.time()

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
                    # Chrome 下载中的临时文件，等它改名成正式文件再处理
                    if file_path.name.lower().endswith(('.crdownload', '.tmp', '.partial')):
                        continue

                    try:
                        file_size = file_path.stat().st_size
                    except Exception:
                        file_size = 0

                    is_valid = (
                        file_path.name.endswith(('.xlsx', '.xls', '.pdf', '.csv')) or
                        file_size > 1000 or
                        (self._is_guid_filename(file_path.name) and file_size > 100)
                    )

                    if is_valid:
                        logger.info(f"确认新下载文件: {file_path}")
                        stable_count = 0
                        last_size = -1
                        vanished = False
                        while stable_count < 3:
                            time.sleep(0.5)
                            if not file_path.exists():
                                # 稳定过程中文件被改名/删除，说明还在下载
                                vanished = True
                                break
                            current_size = file_path.stat().st_size
                            if current_size == last_size:
                                stable_count += 1
                            else:
                                stable_count = 0
                                last_size = current_size
                        if vanished:
                            continue
                        return file_path

        logger.warning("下载超时，未发现新文件")
        return None

    def _move_to_download_path(self, source_file: Path, account: str, end_date: str) -> str:
        """移动到归档目录并按规范重命名"""
        timestamp = datetime.now().strftime("%Y%m%d")
        ext = source_file.suffix or ".xlsx"
        file_name = f"工商银行_银行流水_{timestamp}{ext}"
        file_path = self.archive_path / file_name

        if file_path.exists():
            logger.info(f"文件已存在，将覆盖: {file_path}")

        shutil.move(str(source_file), str(file_path))
        logger.info(f"流水文件已保存: {file_path}")
        return str(file_path)

    JS_SET_DATE_RANGE = """([begin, end]) => {
        const jq = window.jQuery || window.$;
        const apply = (sel, val) => {
            const el = document.querySelector(sel);
            if (!el) return false;
            // 控件是 jQuery datepicker，只改 value 不更新内部状态，
            // 点「查询」时会被内部状态覆盖回默认值
            if (jq) {
                try { jq(sel).datepicker('setDate', val); } catch (e) {}
            }
            el.removeAttribute('readonly');
            el.value = val;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            if (jq) { jq(el).trigger('change'); }
            return true;
        };
        const bVisible = apply('#datepicker1-beginDate', begin);
        const eVisible = apply('#datepicker1-endDate', end);
        const bHidden = apply("input[name='Begin_date']", begin);
        const eHidden = apply("input[name='End_date']", end);
        return {
            bVisible, eVisible, bHidden, eHidden,
            beginVisible: document.querySelector('#datepicker1-beginDate') && document.querySelector('#datepicker1-beginDate').value,
            endVisible: document.querySelector('#datepicker1-endDate') && document.querySelector('#datepicker1-endDate').value,
            beginHidden: document.querySelector("input[name='Begin_date']") && document.querySelector("input[name='Begin_date']").value,
            endHidden: document.querySelector("input[name='End_date']") && document.querySelector("input[name='End_date']").value,
        };
    }"""

    JS_READ_DATE_RANGE = """() => ({
        beginVisible: (document.querySelector('#datepicker1-beginDate') || {}).value,
        endVisible: (document.querySelector('#datepicker1-endDate') || {}).value,
        beginHidden: (document.querySelector("input[name='Begin_date']") || {}).value,
        endHidden: (document.querySelector("input[name='End_date']") || {}).value,
    })"""

    def _set_date_range(self, start_date: str, end_date: str):
        """设置日期范围。控件是 jQuery datepicker，直接改 value 会被内部状态覆盖，
        所以先像用户一样键盘输入，再把隐藏提交字段一起写上。"""
        logger.info(f"设置日期范围: {start_date} 至 {end_date}")
        begin = str(start_date).replace("-", "")
        end = str(end_date).replace("-", "")

        typed = self._type_date("#datepicker1-beginDate", begin)
        typed = self._type_date("#datepicker1-endDate", end) and typed

        try:
            state = self.root.evaluate(self.JS_SET_DATE_RANGE, [begin, end])
            logger.info(f"日期设置(键盘输入={typed}): {state}")
        except Exception as exc:
            logger.warning(f"设置日期失败: {exc}")

    def _type_date(self, selector: str, value: str) -> bool:
        """在业务 iframe 里键盘输入日期；控件有内部状态，光写 value 会被覆盖。"""
        for frame in self.page.frames:
            try:
                loc = frame.locator(selector).first
                if loc.count() == 0:
                    continue
                loc.click(timeout=3000)
                time.sleep(0.3)
                self.page.keyboard.press("Control+A")
                self.page.keyboard.type(value, delay=40)
                self.page.keyboard.press("Tab")
                time.sleep(0.3)
                return True
            except Exception as exc:
                logger.debug(f"输入日期失败 {selector}: {exc}")
                continue
        return False





    def _set_date_field(self, field_name: str, date_value: str):
        """兼容旧调用：改为按工行实际控件写日期。"""
        mapping = {
            "startDate": "#datepicker1-beginDate",
            "endDate": "#datepicker1-endDate",
        }
        selector = mapping.get(field_name)
        if selector:
            self._type_date(selector, str(date_value).replace("-", ""))



    QUERY_SELECTORS = [
        "#qryBtn",
        "button[id*='qry']",
        "input[id*='qry'][type='button']",
        "button:has-text('查询')",
        "input[type='button'][value*='查询']",
        "a:has-text('查询')",
        "button[type='submit']",
    ]

    QUERY_SELECTORS = [
        "#qryBtn",
        "button[id*='qry']",
        "input[id*='qry'][type='button']",
        "button:has-text('查询')",
        "input[type='button'][value*='查询']",
        "button[type='submit']",
    ]

    # 工行业务页在 ICBCINBSReqServlet 这个 iframe 里；首页也有「查询」字样，不能乱点
    BUSINESS_FRAME_MARKS = ("ICBCINBSReqServlet", "dse_operationName")

    def _business_frames(self):
        frames = []
        for frame in self.page.frames:
            url = frame.url or ""
            if any(mark in url for mark in self.BUSINESS_FRAME_MARKS):
                frames.append(frame)
        return frames

    def _click_query(self) -> bool:
        """点击查询按钮。只在业务 iframe 里找，避免点到首页的同名链接。"""
        logger.info("点击查询按钮...")
        clicked = False
        for frame in self._business_frames():
            for selector in self.QUERY_SELECTORS:
                try:
                    loc = frame.locator(selector).first
                    if loc.count() == 0:
                        continue
                    try:
                        loc.click(timeout=2000)
                    except Exception:
                        loc.evaluate("el => el.click()")
                    logger.info(f"已点击查询按钮: {selector}")
                    clicked = True
                    break
                except Exception as exc:
                    logger.debug(f"点击 {selector} 失败: {exc}")
            if clicked:
                break

        if not clicked:
            logger.warning(f"未找到查询按钮，业务帧数={len(self._business_frames())}")
            return False

        # 查询是整页刷新，立刻读会读到空表，轮询等结果渲染
        deadline = time.time() + 12
        while time.time() < deadline:
            try:
                if self.root.locator("table tbody tr").count() > 0:
                    logger.info("查询结果已渲染")
                    break
            except Exception:
                pass
            time.sleep(1)

        # 日期被页面重置成默认区间的话，查的就是默认那段，结果会对不上
        try:
            state = self.root.evaluate(self.JS_READ_DATE_RANGE)
            logger.info(f"查询后日期字段: {state}")
        except Exception:
            pass
        return True







    def _open_download_excel(self) -> bool:
        """点「下载」并选择 Excel。面板可能因点击早于事件绑定而没弹出，重新点。"""
        deadline = time.time() + 8
        while time.time() < deadline:
            for frame in self.page.frames:
                try:
                    if frame.evaluate("() => typeof window['detailDownXlsx'] === 'function'"):
                        frame.evaluate("() => window['detailDownXlsx']()")
                        logger.info("已直接调用 detailDownXlsx()")
                        return True
                except Exception:
                    continue

            for selector in (
                "div.ebankc-mcp-btns > button:nth-child(4)",
                "button:has-text('下载')",
                "a:has-text('下载')",
            ):
                try:
                    btn = self.root.locator(selector).first
                    if btn.count() > 0:
                        btn.evaluate("el => el.click()")
                        logger.info(f"已点击下载按钮: {selector}")
                        break
                except Exception:
                    continue
            time.sleep(1.5)
        return False

    def _statement_file_has_rows(self, path) -> bool:
        """查询为空时工行照样下载一个只有标记行和表头的表，不能当成有数据。"""
        import io

        try:
            from openpyxl import load_workbook

            # 下载下来是无后缀的 GUID 文件名，openpyxl 会按扩展名拒绝，改用流读取
            with open(path, "rb") as handle:
                workbook = load_workbook(io.BytesIO(handle.read()), data_only=True)
            rows = [
                row
                for row in workbook.active.iter_rows(values_only=True)
                if any(cell not in (None, "") for cell in row)
            ]
            workbook.close()
        except Exception:
            return True  # 读不出来按有数据处理，别把正常结果吞掉

        for index, row in enumerate(rows):
            cells = {str(cell) for cell in row if cell is not None}
            if "交易时间" in cells and "摘要" in cells:
                return len(rows) > index + 1
        return True

    def _download_statement(self, account: str, start_date: str, end_date: str) -> Optional[str]:
        """下载单个账户的流水"""
        logger.info(f"正在处理账户 {account} 的流水...")

        self._set_date_range(start_date, end_date)

        if not self._click_query():
            return None

        if not self._check_has_data():
            logger.info("当前查询结果为空")
            return self._create_no_data_marker(start_date, end_date)

        logger.info("检测到数据，开始下载...")
        before_files = self._get_downloaded_files_before()

        if not self._open_download_excel():
            logger.warning("未找到下载按钮")
            return None

        file_path = self._wait_and_find_new_file(before_files, timeout=60)
        if not file_path:
            logger.warning("等待下载超时")
            return None

        if not self._statement_file_has_rows(file_path):
            # 导出有时赶在结果表格渲染完之前，拿到的只有表头；等一会儿再导一次
            logger.info("下载到的文件只有表头，等待后重试一次导出")
            try:
                file_path.unlink()
            except Exception:
                pass
            time.sleep(3)
            retry_before = self._get_downloaded_files_before()
            if self._open_download_excel():
                retry_path = self._wait_and_find_new_file(retry_before, timeout=60)
                if retry_path and self._statement_file_has_rows(retry_path):
                    logger.info("重试导出成功，已拿到数据")
                    return self._move_to_download_path(retry_path, account, end_date)
                if retry_path:
                    try:
                        retry_path.unlink()
                    except Exception:
                        pass
            logger.info("重试后仍无数据，按查询结果为空处理")
            return self._create_no_data_marker(start_date, end_date)

        return self._move_to_download_path(file_path, account, end_date)


