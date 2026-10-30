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
            # 导航到明细查询页面（仅通过菜单点击）
            self._navigate_to_statement_page()
            
            # 不选择单位和账号，直接下载默认账号流水
            file_path = self._download_statement("", start_date_str, end_date_str)
            if file_path:
                downloaded_files.append(file_path)
                    
        except Exception as e:
            logger.error(f"采集流水过程出错: {e}")
        
        logger.info(f"流水采集完成，共下载 {len(downloaded_files)} 个文件")
        return downloaded_files
    
    def _navigate_to_statement_page(self):
        """导航到交易明细查询页面 - 仅通过菜单点击"""
        logger.info("导航到交易明细查询页面...")
        time.sleep(2)
        
        # # 先处理可能的弹窗
        # try:
        #     restore_btn = self.page.locator("button:has-text('恢复'), .restore-btn, [role='dialog'] button").first
        #     if restore_btn.count() > 0 and restore_btn.is_visible(timeout=2000):
        #         logger.info("检测到恢复页面弹窗，点击关闭")
        #         self.page.keyboard.press("Escape")
        #         time.sleep(0.5)
        # except Exception:
        #     pass
        
        # # 尝试点击页面空白处关闭可能展开的菜单
        # try:
        #     self.page.mouse.click(100, 100)
        #     time.sleep(0.5)
        # except Exception:
        #     pass
        
        # 通过左侧图标菜单进入"报表中心"→"银行账户明细"
        try:
            logger.info("通过菜单导航到银行账户明细页面...")
            
            # 1. 点击左侧"报表中心"图标菜单（tooltip）
            report_menu_selectors = [
                '[title="报表中心"]',
                'text=报表中心',
                ':has-text("报表中心")',
            ]
            
            report_menu_clicked = False
            for selector in report_menu_selectors:
                try:
                    elem = self.page.locator(selector).first
                    elem.click(timeout=5000)
                    logger.info(f"点击报表中心菜单: {selector}")
                    report_menu_clicked = True
                    time.sleep(2)  # 等面板展开
                    break
                except Exception:
                    continue
            
            if not report_menu_clicked:
                logger.warning("未找到报表中心菜单，尝试直接点击左侧图标...")
                # 兜底：点击左侧第4个图标
                try:
                    icons = self.page.locator('aside .el-menu-item, aside [class*="menu-item"], aside svg, aside i').all()
                    if len(icons) >= 4:
                        icons[3].click()
                        time.sleep(2)
                        report_menu_clicked = True
                except Exception:
                    pass
            
            # 2. 点击"银行账户明细"（精确匹配，排除"银行账户明细维护"）
            detail_menu_clicked = False
            debug_dir = Path("debug")
            debug_dir.mkdir(exist_ok=True)
            try:
                # 调试：保存菜单展开后的页面
                ts = int(time.time())
                try:
                    self.page.screenshot(path=str(debug_dir / ("menu_open_" + str(ts) + ".png")))
                    with open(debug_dir / ("menu_open_" + str(ts) + ".html"), "w", encoding="utf-8") as f:
                        f.write(self.page.content())
                    logger.info("调试文件已保存: debug/menu_open_" + str(ts) + ".*")
                except Exception as e_dbg:
                    logger.warning("保存调试文件失败: " + str(e_dbg))

                # 方法1: 只点击三级菜单中 span 文本精确等于“银行账户明细”的菜单项，避免点到“银行账户明细维护”
                try:
                    js_result = self.page.evaluate("""() => {
                        var spans = document.querySelectorAll(
                            '.sider-menu-list-second li.second-menu-item-level-3 span.ellpsis-text'
                        );
                        for (var i = 0; i < spans.length; i++) {
                            var span = spans[i];
                            var text = (span.innerText || span.textContent || '').trim();
                            if (text === '银行账户明细') {
                                var item = span.closest('li.second-menu-item-level-3');
                                if (!item) continue;
                                var itemText = (item.innerText || item.textContent || '').trim();
                                if (itemText !== '银行账户明细') continue;
                                var target = item.querySelector('.second-menu-item-text') || item;
                                var rect = target.getBoundingClientRect();
                                if (rect.width <= 0 || rect.height <= 0) continue;
                                target.click();
                                return {
                                    found: true,
                                    method: 'exact_level3_span',
                                    text: text,
                                    itemText: itemText,
                                    x: Math.round(rect.left + rect.width / 2),
                                    y: Math.round(rect.top + rect.height / 2)
                                };
                            }
                        }

                        var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
                        var node;
                        while (node = walker.nextNode()) {
                            if (node.textContent.trim() === '银行账户明细') {
                                var item = node.parentElement ? node.parentElement.closest('li.second-menu-item-level-3') : null;
                                if (!item) continue;
                                var itemText = (item.innerText || item.textContent || '').trim();
                                if (itemText !== '银行账户明细') continue;
                                var target = item.querySelector('.second-menu-item-text') || item;
                                var rect = target.getBoundingClientRect();
                                if (rect.width <= 0 || rect.height <= 0) continue;
                                target.click();
                                return {
                                    found: true,
                                    method: 'exact_text_node_level3',
                                    text: node.textContent.trim(),
                                    itemText: itemText
                                };
                                }
                        }
                        return {found: false};
                    }""")
                    if js_result.get('found'):
                        logger.info("JS TreeWalker 点击: " + str(js_result))
                        time.sleep(4)
                        if self._is_statement_page():
                            detail_menu_clicked = True
                            return
                        logger.warning("精确菜单点击后未进入银行账户明细页面，继续尝试其它定位方式")
                except Exception as e1:
                    logger.warning("JS TreeWalker 失败: " + str(e1))

                # 方法2: Playwright exact 匹配 + 祖先验证（过滤"维护"）
                if not detail_menu_clicked:
                    candidates = self.page.get_by_text("银行账户明细", exact=True).all()
                    logger.info("get_by_text(exact=True) 找到 " + str(len(candidates)) + " 个候选元素")
                    for idx, elem in enumerate(candidates):
                        try:
                            # 用 JS 检查该元素自身及其父元素的完整文本
                            check = elem.evaluate("""el => {
                                var self = (el.innerText || '').trim();
                                var p = el.parentElement;
                                var gp = p ? p.parentElement : null;
                                var pText = p ? (p.innerText || '').trim() : '';
                                var gpText = gp ? (gp.innerText || '').trim() : '';
                                return {
                                    self: self,
                                    pText: pText,
                                    gpText: gpText,
                                    hasWeihu: self.includes('维护') || pText.includes('维护') || gpText.includes('维护')
                                };
                            }""")
                            logger.info("候选" + str(idx+1) + " check=" + str(check))
                            if check.get('hasWeihu'):
                                logger.warning("候选" + str(idx+1) + " 包含'维护'，跳过")
                                continue
                            elem.click(timeout=5000)
                            logger.info("已点击候选 " + str(idx+1))
                            time.sleep(4)
                            try:
                                self.page.screenshot(path=str(debug_dir / ("after_click_" + str(ts) + "_" + str(idx) + ".png")))
                                with open(debug_dir / ("after_click_" + str(ts) + "_" + str(idx) + ".html"), "w", encoding="utf-8") as f:
                                    f.write(self.page.content())
                            except Exception:
                                pass
                            if self._is_statement_page():
                                return
                            logger.warning("候选 " + str(idx+1) + " 点击后未进入正确页面")
                        except Exception:
                            continue

                # 方法3: JS 兜底，innerText 精确匹配 + 祖先过滤
                if not detail_menu_clicked:
                    js_result = self.page.evaluate("""() => {
                        var all = document.querySelectorAll('body *');
                        for (var i=0; i<all.length; i++) {
                            var el = all[i];
                            var txt = (el.innerText || el.textContent || '').trim();
                            if (txt === '银行账户明细') {
                                var p = el.parentElement;
                                var gp = p ? p.parentElement : null;
                                var pTxt = p ? p.innerText.trim() : '';
                                var gpTxt = gp ? gp.innerText.trim() : '';
                                if (pTxt.includes('维护') || gpTxt.includes('维护')) continue;
                                el.click();
                                return {found: true, tag: el.tagName, text: txt};
                            }
                        }
                        return {found: false};
                    }""")
                    if js_result.get('found'):
                        logger.info("JS 兜底点击: " + str(js_result))
                        time.sleep(4)
                        if self._is_statement_page():
                            detail_menu_clicked = True
                            return
                        logger.warning("JS 兜底点击后未进入银行账户明细页面")
            except Exception as e:
                logger.warning("点击银行账户明细菜单失败: " + str(e))

            if not detail_menu_clicked:
                logger.warning("未找到银行账户明细菜单")
                raise Exception("未能通过菜单导航进入银行账户明细页面")
        except Exception as e:
            logger.error("菜单导航失败: " + str(e))
            # 继续抛出：导航失败后不能沿用错误页面继续执行查询/下载
            raise
    
    def _is_statement_page(self) -> bool:
        """检查当前是否在交易明细查询页面 - 宁波银行适配"""
        # 先检查是否进入了错误的"银行账户明细维护"页面。
        # 以顶部主标签栏的【激活页签】为准——未激活的"银行账户明细维护"页签
        # 只是残留 tab（文本同样可见），不能据此误判当前页面。
        try:
            wrong_page = self.page.evaluate("""() => {
                // 主标签栏页签带有 .main-label-home / .main-label-other 自定义结构，
                // 页面内容里的其它 el-tabs（如"已处理/当日"）没有该结构
                var actives = document.querySelectorAll('.el-tabs__item.is-active');
                for (var i = 0; i < actives.length; i++) {
                    var label = actives[i].querySelector('.main-label-other .text, .main-label-home .text');
                    if (!label) continue;
                    var t = (label.innerText || label.textContent || '').trim();
                    if (t === '银行账户明细维护') return true;
                    if (t === '银行账户明细') return false;
                }
                // 无法识别激活页签时退回旧逻辑：排除侧边菜单和标签栏后查找可见的"维护"标题
                var nodes = Array.from(document.querySelectorAll('body *'));
                return nodes.some(function(el) {
                    var text = (el.innerText || el.textContent || '').trim();
                    if (text !== '银行账户明细维护') return false;
                    if (el.closest('.sider-menu-list-second, .sider-menu-ul, aside, .el-tabs__header')) return false;
                    var rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                });
            }""")
            if wrong_page:
                logger.warning("检测到错误页面: 银行账户明细维护")
                return False
        except Exception:
            pass
        
        # 检测所有 frame（包括iframe）
        contexts = [self.page] + list(self.page.frames)
        
        # 只使用"银行账户明细"页面的特有元素作为判断指标
        indicators = [
            'input[placeholder*="起始时间"]',  # 起始时间
            'input[placeholder*="终止时间"]',  # 终止时间
            'input[placeholder*="开始日期"]',  # 兼容旧版
            'input[placeholder*="结束日期"]',  # 兼容旧版
            'input[placeholder="请选择交易日期"]',  # 财资云实际placeholder
            'label[for="startTime"]',
            'label[for="endTime"]',
            '.eb-range-input',
            '.eb-date-editor--daterange',
            'input[name="startDate"]',
            'input[name="endDate"]',
            'input[name="beginDate"]',
            '#startDate',
            '#endDate',
            "button:has-text('查询')",
            "button:has-text('下载')",
        ]
        
        for context in contexts:
            for indicator in indicators:
                try:
                    locator = context.locator(indicator).first
                    if locator.is_visible(timeout=2000):
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
    
    def _check_has_data(self, timeout: int = 30) -> bool:
        """检查查询结果是否有数据

        注意：查询结果表格在报表 iframe（tmsBill）内，Playwright 的 page.locator
        无法穿透 iframe，必须在报表 frame 中检测"暂无数据"。
        查询为异步加载，此处轮询等待结果稳定：出现数据行 -> 有数据；
        出现可见的空状态提示 -> 无数据。
        """
        frame = self._get_report_frame()
        deadline = time.time() + timeout
        last_state = None

        while time.time() < deadline:
            try:
                state = frame.evaluate("""() => {
                    function isVisible(el) {
                        if (!el) return false;
                        var rect = el.getBoundingClientRect();
                        if (rect.width <= 0 || rect.height <= 0) return false;
                        var style = window.getComputedStyle(el);
                        return style.display !== 'none' && style.visibility !== 'hidden';
                    }
                    var loading = Array.from(document.querySelectorAll('.el-loading-mask')).some(isVisible);
                    var rows = Array.from(document.querySelectorAll('.el-table__body tr.el-table__row, table tbody tr.el-table__row')).filter(isVisible).length;
                    var emptyEl = Array.from(document.querySelectorAll('.el-table__empty-text, .el-empty__description, .el-empty-text')).find(isVisible);
                    var emptyText = emptyEl ? (emptyEl.innerText || emptyEl.textContent || '').trim() : '';
                    return {loading: loading, rows: rows, emptyText: emptyText};
                }""")
                last_state = state

                if state.get('loading'):
                    time.sleep(1)
                    continue
                if state.get('rows', 0) > 0:
                    logger.info(f"查询到 {state['rows']} 条流水记录")
                    return True
                if state.get('emptyText'):
                    logger.info(f"查询结果为空（{state['emptyText']}）")
                    return False
            except Exception as e:
                logger.debug(f"检测查询结果状态失败: {e}")
            time.sleep(1)

        # 超时：既没看到数据行也没看到空状态，按"有数据"处理，
        # 走原有下载流程兜底（下载失败会返回 None，不会误生成无数据凭证）
        logger.warning(f"等待查询结果超时，最后状态: {last_state}，按有数据处理并尝试下载")
        return True
    
    def _get_downloaded_files_before(self):
        return get_downloaded_files_before(self.browser_download_path)

    def _wait_and_find_new_file(self, before_files, timeout: int = 60):
        return wait_and_find_new_file(before_files, timeout)

    def _move_to_download_path(self, source_file: Path, account: str, end_date: str) -> str:
        """将文件移动到归档目录，按指定格式重命名"""
        timestamp = datetime.now().strftime("%Y%m%d")

        # 检查配置文件中的格式设置
        file_format = getattr(self.config, 'file_format', 'excel').lower()
        expected_ext = '.xls' if file_format == 'xls' else '.xlsx'

        ext = source_file.suffix or expected_ext
        # 服务端可能返回 .tmp/.crdownload 等临时后缀，按配置格式归档
        if ext.lower() in ('.tmp', '.crdownload', '.download'):
            logger.info(f"源文件为临时后缀 {ext}，按配置格式修正为 {expected_ext}")
            ext = expected_ext
        
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
        
        time.sleep(1)
        
        page_or_frame = self._get_report_frame()
        
        try:
            # 设置开始日期和结束日期（宁波银行适配）
            self._set_date_field_by_playwright("startDate", start_date, page_or_frame)
            self._set_date_field_by_playwright("beginDate", start_date, page_or_frame)
            self._set_date_field_by_playwright("endDate", end_date, page_or_frame)
            
            # 关闭日历
            self.page.keyboard.press("Escape")
            time.sleep(0.2)
            self.page.keyboard.press("Escape")
            time.sleep(0.2)
            
            # 点击空白处关闭日历（避免无效选择器 timeout）
            try:
                page_or_frame.evaluate("() => { document.activeElement && document.activeElement.blur(); }")
                time.sleep(0.2)
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
                'startDate': '起始时间',
                'beginDate': '起始时间',
                'endDate': '终止时间',
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
            
            # 财资云实际页面：label for="startTime"/"endTime"，input placeholder="请选择交易日期"
            if field_id in ('startDate', 'beginDate'):
                selectors.extend([
                    'xpath=//label[@for="startTime"]/following::input[@placeholder="请选择交易日期"][1]',
                    'css=.el-form-item:has(label[for="startTime"]) input.el-input__inner',
                ])
            elif field_id == 'endDate':
                selectors.extend([
                    'xpath=//label[@for="endTime"]/following::input[@placeholder="请选择交易日期"][1]',
                    'css=.el-form-item:has(label[for="endTime"]) input.el-input__inner',
                ])
            
            input_field = None
            used_selector = None
            for selector in selectors:
                try:
                    elem = context.locator(selector).first
                    if elem.is_visible(timeout=3000):
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
    
    def _get_report_frame(self):
        """定位报表 iframe（按钮、日期输入框都在这个 iframe 里）

        注意：顶部页签可同时打开"银行账户明细维护"和"银行账户明细"两个报表页，
        frames 列表中"维护"iframe 通常排在前面，必须优先匹配明细查询页
        （realUserDetailList / bank_account_details），并排除 Maintenance。
        """
        frames = list(self.page.frames)
        # 第一优先：明确的明细查询页面
        for frame in frames:
            url = frame.url or ''
            if ('realUserDetailList' in url or 'bank_account_details' in url) and 'Maintenance' not in url:
                logger.info(f"找到报表 iframe: {url[:80]}")
                return frame
        # 第二优先：tmsBill 报表 iframe，但排除"维护"页
        for frame in frames:
            url = frame.url or ''
            if ('tms_report_web' in url or 'tmsBill' in url) and 'Maintenance' not in url:
                logger.info(f"找到报表 iframe: {url[:80]}")
                return frame
        # 兜底：返回第一个非主页面、有有效 URL 且不是 chrome-error 的 iframe
        for frame in frames:
            url = frame.url or ''
            if frame != self.page.main_frame and url and not url.startswith('chrome-error://'):
                logger.info(f"使用第一个非主 iframe: {url[:80]}")
                return frame
        logger.warning("未找到报表 iframe，使用主页面")
        return self.page

    def _set_hanging_method(self, method: str = "全部") -> bool:
        """设置加挂方式 - 宁波银行财资云适配"""
        logger.info(f"设置加挂方式为: {method}")

        # 加挂方式可能在主页面也可能在报表 iframe 中，依次尝试
        contexts = [self.page, self._get_report_frame()]
        opened_context = None

        # 1. 先找到 label 并点击打开下拉框
        for ctx in contexts:
            try:
                opened = ctx.evaluate("""() => {
                    var labels = document.querySelectorAll('label, .el-form-item__label');
                    var targetLabel = null;
                    for (var i = 0; i < labels.length; i++) {
                        var t = (labels[i].innerText || labels[i].textContent || '').trim();
                        if (t === '加挂方式' || t.includes('加挂方式')) {
                            targetLabel = labels[i];
                            break;
                        }
                    }
                    if (!targetLabel) return {success: false, error: 'label not found'};

                    var formItem = targetLabel.closest('.el-form-item');
                    if (!formItem) return {success: false, error: 'form item not found'};

                    var select = formItem.querySelector('.el-select');
                    if (!select) return {success: false, error: 'select not found'};

                    var input = select.querySelector('.el-input__inner') || select.querySelector('input') || select;
                    input.click();
                    input.focus();
                    return {success: true};
                }""")
                if opened.get('success'):
                    opened_context = ctx
                    logger.info(f"已在 {'iframe' if ctx != self.page else '主页面'} 中打开加挂方式下拉框")
                    break
                else:
                    logger.debug(f"{'iframe' if ctx != self.page else '主页面'} 中未找到加挂方式: {opened}")
            except Exception as e:
                logger.debug(f"{'iframe' if ctx != self.page else '主页面'} 打开加挂方式失败: {e}")

        if not opened_context:
            logger.warning("打开加挂方式下拉框失败: 主页面和 iframe 均未找到 label")
            return False

        time.sleep(1.5)

        # 2. 在弹出的下拉列表中点击目标选项
        # 策略：找到最近一次出现、且包含预期选项的 dropdown，再点击目标
        for ctx in contexts:
            try:
                clicked = ctx.evaluate("""(method) => {
                    // Element UI 下拉菜单挂载在 body 上，可能有多个同时存在
                    // 优先选择包含预期选项集且可见的 dropdown
                    var dropdowns = document.querySelectorAll('.el-select-dropdown');
                    var targetDropdown = null;

                    for (var d = 0; d < dropdowns.length; d++) {
                        var dd = dropdowns[d];
                        var rect = dd.getBoundingClientRect();
                        var style = window.getComputedStyle(dd);
                        if (style.display === 'none' || style.visibility === 'hidden' || rect.width === 0) continue;

                        var items = dd.querySelectorAll('.el-select-dropdown__item');
                        var itemTexts = [];
                        var hasTarget = false;
                        for (var i = 0; i < items.length; i++) {
                            var txt = (items[i].innerText || items[i].textContent || '').trim();
                            itemTexts.push(txt);
                            if (txt === method || txt.includes(method)) hasTarget = true;
                        }
                        // 如果包含目标选项，且包含加挂方式的特征选项（在线账号/离线账号等），就认为是目标 dropdown
                        var isHangingDropdown = itemTexts.some(function(t) {
                            return t === '在线账号' || t === '离线账号' || t === 'RPA账号' || t === 'SWIFT账号';
                        });
                        if (hasTarget && isHangingDropdown) {
                            targetDropdown = dd;
                            break;
                        }
                    }

                    // 兜底：如果没有识别到特征 dropdown，使用最后一个可见 dropdown
                    if (!targetDropdown) {
                        for (var d = dropdowns.length - 1; d >= 0; d--) {
                            var dd = dropdowns[d];
                            var style = window.getComputedStyle(dd);
                            if (style.display !== 'none' && style.visibility !== 'hidden') {
                                targetDropdown = dd;
                                break;
                            }
                        }
                    }

                    if (!targetDropdown) return {success: false, error: 'no visible dropdown'};

                    var items = targetDropdown.querySelectorAll('.el-select-dropdown__item');
                    for (var i = 0; i < items.length; i++) {
                        var text = (items[i].innerText || items[i].textContent || '').trim();
                        if (text === method) {
                            items[i].click();
                            return {success: true, text: text, match: 'exact'};
                        }
                    }
                    for (var i = 0; i < items.length; i++) {
                        var text = (items[i].innerText || items[i].textContent || '').trim();
                        if (text.includes(method)) {
                            items[i].click();
                            return {success: true, text: text, match: 'contains'};
                        }
                    }
                    return {success: false, error: 'option not found', available: Array.from(items).map(el => (el.innerText || '').trim())};
                }""", method)

                if clicked.get('success'):
                    logger.info(f"已选择加挂方式: {method} ({clicked.get('match')})")
                    time.sleep(0.5)
                    return True
                else:
                    logger.debug(f"{'iframe' if ctx != self.page else '主页面'} 中未找到选项: {clicked}")
            except Exception as e:
                logger.debug(f"{'iframe' if ctx != self.page else '主页面'} 选择加挂方式失败: {e}")

        logger.warning(f"未找到加挂方式选项 '{method}'")
        return False

    def _click_button_by_js(self, button_text: str) -> bool:
        """在报表 iframe 中用 JS 查找包含指定文本的可见按钮并点击"""
        frame = self._get_report_frame()
        try:
            clicked = frame.evaluate("""(text) => {
                var buttons = document.querySelectorAll('button, a, input[type="button"]');
                // 第一轮：精确匹配
                for (var j = 0; j < buttons.length; j++) {
                    var btn = buttons[j];
                    var btnText = (btn.innerText || btn.textContent || btn.value || '').trim();
                    if (btnText === text && btn.offsetParent !== null) {
                        btn.click();
                        return {clicked: true, text: btnText, tag: btn.tagName, match: 'exact'};
                    }
                }
                // 第二轮：包含匹配（排除已精确匹配过的）
                for (var j = 0; j < buttons.length; j++) {
                    var btn = buttons[j];
                    var btnText = (btn.innerText || btn.textContent || btn.value || '').trim();
                    if (btnText.includes(text) && btn.offsetParent !== null) {
                        btn.click();
                        return {clicked: true, text: btnText, tag: btn.tagName, match: 'contains'};
                    }
                }
                return {clicked: false};
            }""", button_text)
            if clicked and clicked.get('clicked'):
                logger.info(f"JS 点击成功: [{clicked.get('tag')}] '{clicked.get('text')}' ({clicked.get('match')})")
                return True
            logger.warning(f"JS 未找到包含 '{button_text}' 的可见按钮")
            return False
        except Exception as e:
            logger.warning(f"JS 点击 '{button_text}' 失败: {e}")
            return False

    def _click_confirm_ok_by_js(self) -> bool:
        """在报表 iframe 中用 JS 点击确认弹窗中的确定按钮"""
        frame = self._get_report_frame()
        try:
            clicked = frame.evaluate("""() => {
                var dialogs = document.querySelectorAll('.el-dialog__wrapper, [role="dialog"], .confirm-dialog, .cz-message-box, .el-message-box__wrapper');
                for (var d = 0; d < dialogs.length; d++) {
                    var btns = dialogs[d].querySelectorAll('button, .el-button--primary');
                    for (var b = 0; b < btns.length; b++) {
                        var txt = (btns[b].innerText || btns[b].textContent || '').trim();
                        if ((txt.includes('确定') || txt.includes('确认')) && btns[b].offsetParent !== null) {
                            btns[b].click();
                            return {clicked: true, text: txt};
                        }
                    }
                }
                var allBtns = document.querySelectorAll('button, .el-button--primary');
                for (var b = 0; b < allBtns.length; b++) {
                    var txt = (allBtns[b].innerText || allBtns[b].textContent || '').trim();
                    if ((txt.includes('确定') || txt.includes('确认')) && allBtns[b].offsetParent !== null) {
                        allBtns[b].click();
                        return {clicked: true, text: txt};
                    }
                }
                return {clicked: false};
            }""")
            if clicked and clicked.get('clicked'):
                logger.info(f"JS 点击确定按钮成功: '{clicked.get('text')}'")
                return True
            return False
        except Exception as e:
            logger.warning(f"JS 点击确定按钮失败: {e}")
            return False

    def _click_download_xlsx_option_by_js(self) -> bool:
        """点击下载按钮下拉菜单中的 xlsx 选项"""
        frame = self._get_report_frame()
        try:
            clicked = frame.evaluate("""() => {
                // 查找下拉菜单中的 xlsx 选项
                var items = document.querySelectorAll('li.el-dropdown-menu__item, .el-dropdown-menu__item, [class*="dropdown"] li, [class*="menu__item"]');
                for (var i = 0; i < items.length; i++) {
                    var txt = (items[i].innerText || items[i].textContent || '').trim();
                    if ((txt.includes('自定义明细') && txt.includes('xlsx')) || txt === '自定义明细 (.xlsx)') {
                        items[i].click();
                        return {clicked: true, text: txt};
                    }
                }
                // 兜底：查找任何包含 xlsx 的可见菜单项
                var allItems = document.querySelectorAll('li, .el-dropdown-menu__item, [class*="menu"] > *');
                for (var i = 0; i < allItems.length; i++) {
                    var txt = (allItems[i].innerText || allItems[i].textContent || '').trim();
                    if (txt.includes('xlsx') && allItems[i].offsetParent !== null) {
                        allItems[i].click();
                        return {clicked: true, text: txt};
                    }
                }
                return {clicked: false};
            }""")
            if clicked and clicked.get('clicked'):
                logger.info(f"JS 点击下载选项成功: '{clicked.get('text')}'")
                return True
            logger.warning("JS 未找到 xlsx 下载选项")
            return False
        except Exception as e:
            logger.warning(f"JS 点击下载选项失败: {e}")
            return False

    def _download_statement(self, account: str, start_date: str, end_date: str) -> Optional[str]:
        """下载单个账户的流水"""
        logger.info(f"正在处理账户 {account} 的流水...")
        
        # 不选择单位和账号，使用页面默认账号
        
        # 等待 iframe 加载稳定
        time.sleep(3)
        
        # 设置日期范围
        self._set_date_range(start_date, end_date)

        # 设置加挂方式为"全部"（宁波银行财资云适配）
        self._set_hanging_method("全部")

        # 再次确认日期值（在报表 iframe 中查找）
        try:
            frame = self._get_report_frame()
            # 财资云实际页面：label for="startTime"/"endTime"，input placeholder="请选择交易日期"
            begin_val = frame.locator('xpath=//label[@for="startTime"]/following::input[1]').first.input_value(timeout=3000)
            end_val = frame.locator('xpath=//label[@for="endTime"]/following::input[1]').first.input_value(timeout=3000)
            if begin_val and end_val:
                logger.info(f"查询前日期确认: {begin_val} 至 {end_val}")
        except Exception:
            pass
        
        # 点击查询按钮
        logger.info("点击查询按钮...")
        try:
            if not self._click_button_by_js("查询"):
                logger.warning("未找到查询按钮")
                return None
            time.sleep(1)
        except Exception as e:
            logger.error(f"点击查询按钮失败: {e}")
            return None
        
        # 检查是否有数据
        if not self._check_has_data():
            logger.info("当前查询结果为空")
            return self._create_no_data_marker(start_date, end_date)
        
        logger.info("检测到数据，开始下载...")
        
        # 点击下载按钮并监听下载
        try:
            before_files = self._get_downloaded_files_before()
            
            # 使用 expect_download 包裹整个流程
            try:
                with self.page.expect_download(timeout=60000) as download_info:
                    # 1. 点击下载按钮（展开下拉菜单）
                    if not self._click_button_by_js("下载"):
                        raise Exception("未找到下载按钮")
                    time.sleep(1)
                    
                    # 2. 点击下拉菜单中的 xlsx 选项
                    if not self._click_download_xlsx_option_by_js():
                        logger.warning("未找到 xlsx 下载选项，尝试直接触发下载...")
                    time.sleep(1)
                    
                    # 3. 处理确认弹窗（如果有）
                    self._click_confirm_ok_by_js()
                    time.sleep(1)
                
                download = download_info.value
                save_name = download.suggested_filename
                # 服务端可能返回 .tmp 等临时后缀，按实际下载的 xlsx 选项修正
                if Path(save_name).suffix.lower() in ('.tmp', '.crdownload'):
                    save_name = Path(save_name).stem + '.xlsx'
                    logger.info(f"建议文件名含临时后缀，已修正为: {save_name}")
                temp_path = self.browser_download_path / save_name
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
                return None
                    
        except Exception as e:
            logger.error(f"下载过程出错: {e}")
            return None
