# -*- coding: utf-8 -*-
"""
离线明细导入模块 - 宁波银行财资云适配

功能：将南京银行生成的离线账户明细导入模板，自动上传到宁波银行财资云的
      "银行账户明细维护 -> 离线明细导入" 页面，并点击"发起识别"。
"""
import time
from datetime import date
from pathlib import Path
from typing import Optional, List
from playwright.sync_api import Page

from logger import setup_logger

logger = setup_logger()


class OfflineStatementImporter:
    """离线明细导入器 - 宁波银行财资云"""

    # 南京银行项目默认路径（可通过配置覆盖）
    DEFAULT_NJCB_PROJECT_PATH = Path(r"c:\wangyingbao\南京银行\njcb_automation")
    DEFAULT_FILENAME_PATTERN = "离线账户明细导入_已填_*.xls"

    def __init__(self, page: Page, config, browser_download_path: Optional[Path] = None):
        """
        Args:
            page: Playwright 页面对象
            config: OfflineImportConfig 配置对象
            browser_download_path: 浏览器下载路径（可选，用于调试/兜底）
        """
        self.page = page
        self.config = config
        self.browser_download_path = browser_download_path

    def import_latest(self) -> dict:
        """
        导入最新的南京银行离线明细模板

        Returns:
            {"success": bool, "file": str, "recognized": bool,
             "recognized_success": bool, "submitted": bool, "error": str}
        """
        result = {
            "success": False,
            "file": "",
            "recognized": False,
            "recognized_success": False,
            "submitted": False,
            "error": "",
        }

        try:
            # 1. 查找最新模板文件
            template_file = self._find_latest_template()
            if not template_file:
                result["error"] = "未找到南京银行离线明细导入模板文件"
                logger.error(result["error"])
                return result

            result["file"] = str(template_file)
            logger.info(f"找到最新导入模板: {template_file}")

            # 2. 导航到离线明细导入页面
            if not self._navigate_to_import_page():
                result["error"] = "导航到离线明细导入页面失败"
                logger.error(result["error"])
                return result

            # 3. 上传文件
            if not self._upload_file(template_file):
                result["error"] = "上传模板文件失败"
                logger.error(result["error"])
                return result

            result["success"] = True
            logger.info("模板文件上传成功")

            # 4. 点击发起识别（如果配置启用）
            if self.config.click_recognize:
                if self._click_recognize():
                    result["recognized"] = True
                    logger.info("已点击发起识别")
                else:
                    logger.warning("未找到或未能点击'发起识别'按钮，文件已上传，请手动点击")
                    return result
            else:
                logger.info("配置 click_recognize=false，跳过点击发起识别")
                return result

            # 5. 等待识别成功（如果配置启用）
            if self.config.wait_recognize_success:
                if self._wait_recognition_success(template_file):
                    result["recognized_success"] = True
                    logger.info("文件识别成功")
                else:
                    logger.warning("等待识别成功超时或失败，请手动处理")
                    return result
            else:
                logger.info("配置 wait_recognize_success=false，跳过等待识别成功")
                return result

            # 6. 点击提交（如果配置启用）
            if self.config.click_submit:
                if self._click_submit():
                    result["submitted"] = True
                    logger.info("已点击提交")
                else:
                    logger.warning("未找到或未能点击'提交'按钮，请手动提交")
            else:
                logger.info("配置 click_submit=false，跳过点击提交")

            return result

        except Exception as e:
            result["error"] = f"离线明细导入异常: {e}"
            logger.error(result["error"])
            import traceback
            logger.error(traceback.format_exc())
            return result

    def _find_latest_template(self) -> Optional[Path]:
        """查找当天最新的南京银行导入模板文件"""
        # 确定搜索目录
        source_dir = self.config.source_directory
        if source_dir:
            search_dir = Path(source_dir)
        else:
            # 默认搜索南京银行项目的 downloads/statements 目录
            search_dir = self.DEFAULT_NJCB_PROJECT_PATH / "downloads" / "statements"

        if not search_dir.exists():
            logger.warning(f"模板搜索目录不存在: {search_dir}")
            return None

        pattern = self.config.filename_pattern or self.DEFAULT_FILENAME_PATTERN
        candidates = list(search_dir.rglob(pattern))

        if not candidates:
            logger.warning(f"在 {search_dir} 下未找到匹配 {pattern} 的模板文件")
            return None

        # 只保留当天创建/修改的文件，避免重复导入历史文件
        today = date.today()
        today_candidates = [
            p for p in candidates
            if date.fromtimestamp(p.stat().st_mtime) == today
        ]

        if not today_candidates:
            logger.info(f"未找到当天创建的模板文件，跳过导入（共 {len(candidates)} 个历史文件）")
            return None

        # 按修改时间取最新
        latest = max(today_candidates, key=lambda p: p.stat().st_mtime)
        logger.info(f"从 {len(today_candidates)} 个当天模板中选中最新文件: {latest}")
        return latest

    def _navigate_to_import_page(self, max_retries: int = 3) -> bool:
        """导航到离线明细导入页面"""
        logger.info("导航到离线明细导入页面...")

        for attempt in range(1, max_retries + 1):
            try:
                # 1. 点击左侧"报表中心"菜单
                self._click_menu_item("报表中心", menu_level="一级")
                time.sleep(1.5)

                # 2. 点击"银行报表"
                self._click_menu_item("银行报表", menu_level="二级")
                time.sleep(1.5)

                # 3. 点击"银行账户明细维护"（使用三级菜单专用选择器）
                if not self._click_level3_menu_item("银行账户明细维护"):
                    logger.warning(f"第 {attempt} 次未能点击银行账户明细维护菜单")
                    continue

                # 等待并确认已进入银行账户明细维护页面
                if not self._wait_for_detail_maintenance_page(timeout=10):
                    logger.warning(f"第 {attempt} 次点击菜单后未进入银行账户明细维护页面")
                    continue

                # 4. 在银行账户明细维护页面中点击"离线明细导入"（toolbar 按钮）
                if not self._click_offline_import_tab():
                    logger.warning(f"第 {attempt} 次未能点击离线明细导入")
                    continue

                # 5. 检查是否到达导入页面
                if self._is_import_page():
                    logger.info("成功进入离线明细导入页面")
                    return True

                logger.warning(f"第 {attempt} 次导航后未检测到导入页面，重试...")

            except Exception as e:
                logger.warning(f"第 {attempt} 次导航失败: {e}")

        logger.error("导航到离线明细导入页面失败，已达最大重试次数")
        self._save_debug_snapshot("navigate_to_import_failed")
        return False

    def _click_level3_menu_item(self, text: str) -> bool:
        """点击左侧三级菜单项（如 银行账户明细维护），优先用 Playwright 真实鼠标事件"""
        logger.info(f"点击三级菜单: {text}")

        # 方法1：Playwright get_by_text(exact=True) + 点击 li 元素（最接近用户真实点击）
        try:
            candidates = self.page.get_by_text(text, exact=True).all()
            logger.info(f"get_by_text(exact=True) 找到 {len(candidates)} 个候选元素")
            for idx, elem in enumerate(candidates):
                try:
                    check = elem.evaluate("""el => {
                        var item = el.closest('li.second-menu-item-level-3');
                        return {inMenu: !!item, tag: item ? item.tagName : ''};
                    }""")
                    if not check.get('inMenu'):
                        logger.debug(f"候选 {idx+1} 不在左侧菜单中，跳过")
                        continue
                    # 点击 li 本身，让 Playwright 模拟真实鼠标事件
                    item = elem.locator('xpath=ancestor::li[contains(@class,"second-menu-item-level-3")]').first
                    item.scroll_into_view_if_needed(timeout=3000)
                    item.click(timeout=5000)
                    logger.info(f"Playwright 点击三级菜单成功: {text}")
                    return True
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"Playwright 三级菜单点击失败: {e}")

        # 方法2：JS 兜底点击
        try:
            result = self.page.evaluate("""(text) => {
                var spans = document.querySelectorAll(
                    '.sider-menu-list-second li.second-menu-item-level-3 span.ellpsis-text'
                );
                for (var i = 0; i < spans.length; i++) {
                    var span = spans[i];
                    var t = (span.innerText || span.textContent || '').trim();
                    if (t === text) {
                        var item = span.closest('li.second-menu-item-level-3');
                        if (!item) continue;
                        var itemText = (item.innerText || item.textContent || '').trim();
                        if (itemText.indexOf(text) === -1) continue;
                        var target = item.querySelector('.second-menu-item-content')
                            || item.querySelector('.second-menu-item-text')
                            || item;
                        var rect = target.getBoundingClientRect();
                        if (rect.width <= 0 || rect.height <= 0) continue;
                        target.scrollIntoView({block: 'center'});
                        target.click();
                        return {
                            found: true, method: 'level3_content', text: t,
                            x: Math.round(rect.left + rect.width / 2),
                            y: Math.round(rect.top + rect.height / 2)
                        };
                    }
                }

                var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
                var node;
                while (node = walker.nextNode()) {
                    if (node.textContent.trim() === text) {
                        var item = node.parentElement ? node.parentElement.closest('li.second-menu-item-level-3') : null;
                        if (!item) continue;
                        var target = item.querySelector('.second-menu-item-content')
                            || item.querySelector('.second-menu-item-text')
                            || item;
                        var rect = target.getBoundingClientRect();
                        if (rect.width <= 0 || rect.height <= 0) continue;
                        target.scrollIntoView({block: 'center'});
                        target.click();
                        return {found: true, method: 'level3_text_node', text: text};
                    }
                }
                return {found: false};
            }""", text)
            if result.get('found'):
                logger.info(f"JS 兜底点击三级菜单成功: {result}")
                return True
        except Exception as e:
            logger.debug(f"JS 三级菜单点击失败: {e}")

        logger.warning(f"未能点击三级菜单: {text}")
        return False

    def _get_report_frame(self):
        """获取银行账户明细维护对应的 iframe"""
        try:
            return self.page.frame_locator('iframe[data-key="BankAccDetailMaintain"]').first
        except Exception as e:
            logger.debug(f"获取报告 iframe 失败: {e}")
            return None

    def _get_active_frame(self):
        """获取当前激活的 iframe（上传页面会打开新的 iframe 并激活）"""
        try:
            return self.page.frame_locator('iframe.active').first
        except Exception as e:
            logger.debug(f"获取 active iframe 失败: {e}")
            return None

    def _get_upload_frame(self):
        """获取上传页面 iframe：优先 active iframe，其次 BankAccDetailMaintain"""
        active = self._get_active_frame()
        if active:
            return active
        return self._get_report_frame()

    def _wait_for_detail_maintenance_page(self, timeout: int = 15) -> bool:
        """等待并检查是否已进入银行账户明细维护页面（iframe 内容加载完成）"""
        text = "银行账户明细维护"
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                frame = self._get_report_frame()
                if not frame:
                    logger.debug("报告 iframe 尚未出现，继续等待...")
                    time.sleep(1)
                    continue

                # 检查 iframe 内是否出现页面内容
                indicators = ["查询条件", "加挂方式", "交易日期", "离线明细导入", "批量操作", "导入记录"]
                for indicator in indicators:
                    try:
                        locator = frame.locator("body").locator("text=" + indicator).first
                        if locator.is_visible(timeout=2000):
                            logger.info(f"银行账户明细维护 iframe 内容已加载，检测到: {indicator}")
                            return True
                    except Exception:
                        continue

                logger.debug("iframe 已出现，内容仍在加载...")
            except Exception as e:
                logger.debug(f"检查页面失败: {e}")
            time.sleep(1)
        logger.warning("等待银行账户明细维护页面超时")
        return False

    def _click_offline_import_tab(self, wait_timeout: int = 15) -> bool:
        """在银行账户明细维护 iframe 中点击'离线明细导入'按钮"""
        text = "离线明细导入"
        logger.info(f"点击'{text}'...")

        deadline = time.time() + wait_timeout
        while time.time() < deadline:
            try:
                frame = self._get_report_frame()
                if not frame:
                    time.sleep(1)
                    continue

                # 方法1：Playwright 在 iframe 中精确匹配按钮
                try:
                    btn = frame.locator("button, .el-button").filter(has_text=text).first
                    if btn.is_visible(timeout=3000):
                        btn.click(timeout=5000)
                        logger.info(f"iframe 中点击 button/.el-button 成功: {text}")
                        return True
                except Exception as e:
                    logger.debug(f"iframe button/.el-button 点击失败: {e}")

                # 方法2：has-text 选择器
                selectors = [
                    f"button:has-text('{text}')",
                    f"a:has-text('{text}')",
                    f".el-button:has-text('{text}')",
                    f"text={text}",
                ]
                for selector in selectors:
                    try:
                        elem = frame.locator(selector).first
                        if elem.is_visible(timeout=2000):
                            elem.click(timeout=5000)
                            logger.info(f"iframe 中点击成功: {selector}")
                            return True
                    except Exception:
                        continue

                # 方法3：JS 在 iframe 内兜底
                try:
                    frame_element = self.page.locator('iframe[data-key="BankAccDetailMaintain"]').first
                    actual_frame = frame_element.content_frame()
                    if actual_frame:
                        result = actual_frame.evaluate("""(text) => {
                            var all = document.querySelectorAll('body *');
                            var candidates = [];
                            for (var i = 0; i < all.length; i++) {
                                var el = all[i];
                                var t = (el.innerText || el.textContent || '').trim();
                                if (t === text || t.includes(text)) {
                                    var rect = el.getBoundingClientRect();
                                    if (rect.width <= 0 || rect.height <= 0) continue;
                                    var clickable = el;
                                    while (clickable && clickable.tagName !== 'BUTTON' && clickable.tagName !== 'A'
                                           && !clickable.classList.contains('el-button')
                                           && !clickable.onclick) {
                                        clickable = clickable.parentElement;
                                    }
                                    if (!clickable) clickable = el;
                                    var cRect = clickable.getBoundingClientRect();
                                    candidates.push({
                                        el: clickable,
                                        area: cRect.width * cRect.height,
                                        x: Math.round(cRect.left + cRect.width / 2),
                                        y: Math.round(cRect.top + cRect.height / 2)
                                    });
                                }
                            }
                            if (candidates.length === 0) return {found: false};
                            candidates.sort(function(a, b) { return b.area - a.area; });
                            var target = candidates[0];
                            target.el.click();
                            return {found: true, clicked: target};
                        }""", text)
                        if result.get('found'):
                            logger.info(f"JS 在 iframe 中点击'{text}'成功: {result}")
                            return True
                except Exception as e:
                    logger.debug(f"JS 在 iframe 中点击失败: {e}")

            except Exception as e:
                logger.debug(f"点击'{text}'过程出错: {e}")
            time.sleep(1)

        logger.warning(f"未能点击'{text}'")
        return False

    def _click_menu_item(self, text: str, menu_level: str = "",
                         exclude_texts: Optional[List[str]] = None) -> bool:
        """
        通用菜单点击：优先在页面/iframe 中查找精确文本并点击
        """
        exclude_texts = exclude_texts or []

        # 先尝试 Playwright 精确匹配
        try:
            candidates = self.page.get_by_text(text, exact=True).all()
            for elem in candidates:
                try:
                    # 排除包含排除文本的祖先元素
                    check = elem.evaluate("""(el, excludeTexts) => {
                        let node = el;
                        while (node) {
                            let t = (node.innerText || node.textContent || '').trim();
                            for (let ex of excludeTexts) {
                                if (t !== '""" + text + """' && t.includes(ex)) return false;
                            }
                            node = node.parentElement;
                        }
                        return true;
                    }""", exclude_texts)
                    if check:
                        elem.click(timeout=5000)
                        logger.info(f"已点击菜单: {text}")
                        return True
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"Playwright 点击菜单 {text} 失败: {e}")

        # 兜底：JS 全文档精确匹配
        try:
            clicked = self.page.evaluate("""(args) => {
                var text = args.text;
                var excludeTexts = args.excludeTexts || [];
                var all = document.querySelectorAll('body *');
                for (var i = 0; i < all.length; i++) {
                    var el = all[i];
                    var t = (el.innerText || el.textContent || '').trim();
                    if (t === text) {
                        // 检查祖先是否包含排除文本
                        var node = el;
                        var skip = false;
                        while (node) {
                            var pt = (node.innerText || node.textContent || '').trim();
                            for (var j = 0; j < excludeTexts.length; j++) {
                                if (pt !== text && pt.includes(excludeTexts[j])) {
                                    skip = true;
                                    break;
                                }
                            }
                            if (skip) break;
                            node = node.parentElement;
                        }
                        if (skip) continue;
                        var rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            el.click();
                            return {clicked: true, tag: el.tagName, text: t};
                        }
                    }
                }
                return {clicked: false};
            }""", {"text": text, "excludeTexts": exclude_texts})

            if clicked.get("clicked"):
                logger.info(f"JS 点击菜单成功: {text}")
                return True
        except Exception as e:
            logger.debug(f"JS 点击菜单 {text} 失败: {e}")

        logger.warning(f"未能点击菜单: {text}")
        return False

    def _is_import_page(self) -> bool:
        """检查当前是否在离线明细导入页面（可能在 active iframe 内）"""
        # 优先检查当前激活的 iframe
        contexts = []
        active_frame = self._get_upload_frame()
        if active_frame:
            try:
                actual_frame = self.page.locator('iframe.active').first.content_frame()
                if actual_frame:
                    contexts.append(actual_frame)
            except Exception:
                pass
        contexts.extend([self.page] + list(self.page.frames))

        indicators = [
            "text=将文件拖到此处",
            "text=点击上传",
            "text=发起识别",
            "text=离线明细导入",
            'input[type="file"]',
            ".el-upload-dragger",
            ".el-upload",
        ]

        for context in contexts:
            for indicator in indicators:
                try:
                    locator = context.locator(indicator).first
                    if locator.is_visible(timeout=2000):
                        logger.debug(f"检测到导入页面特征: {indicator}")
                        return True
                except Exception:
                    continue
        return False

    def _is_file_in_upload_list(self, file_path: Path) -> bool:
        """检查指定文件名是否已存在于上传列表中（防止重复上传）"""
        file_name = file_path.name
        contexts = []

        # 优先检查当前激活的 iframe
        try:
            active_frame = self.page.locator('iframe.active').first
            actual_frame = active_frame.content_frame()
            if actual_frame:
                contexts.append(actual_frame)
        except Exception:
            pass

        # 兜底：主页面和所有 iframe
        contexts.extend([self.page] + list(self.page.frames))

        for context in contexts:
            try:
                found = context.evaluate("""(fileName) => {
                    var all = document.querySelectorAll('body, body *');
                    for (var i = 0; i < all.length; i++) {
                        var t = (all[i].innerText || all[i].textContent || '').trim();
                        if (t.includes(fileName)) return true;
                    }
                    return false;
                }""", file_name)
                if found:
                    logger.info(f"检测到文件已存在于上传列表中: {file_name}")
                    return True
            except Exception:
                continue
        return False

    def _upload_file(self, file_path: Path) -> bool:
        """上传模板文件"""
        logger.info(f"准备上传文件: {file_path}")

        if not file_path.exists():
            logger.error(f"上传文件不存在: {file_path}")
            return False

        # 获取当前导入页面对应的 frame（上传页面会在新 iframe 中打开）
        frame = self._get_upload_frame()
        if not frame:
            logger.warning("未找到上传 iframe，尝试在主页面中上传")

        # 上传前检查是否已存在同名文件，避免重复上传
        if self._is_file_in_upload_list(file_path):
            logger.info("上传列表中已存在该文件，跳过本次上传")
            return True

        # 方法1：直接查找 input[type="file"] 并 set_input_files
        try:
            file_input = self._find_file_input()
            if file_input:
                file_input.set_input_files(str(file_path))
                logger.info("已通过 input[type=file] 上传文件")
                time.sleep(2)
                if self._wait_upload_complete(file_path):
                    return True
                # 等待超时但文件可能已上传成功，检查列表避免进入方法2导致重复
                if self._is_file_in_upload_list(file_path):
                    logger.info("input 上传后检测到文件已存在列表中，视为成功")
                    return True
            else:
                logger.warning("方法1：未找到 input[type=file]")
        except Exception as e:
            logger.warning(f"方法1上传失败: {e}")

        # 方法1失败后、方法2开始前再次检查，防止方法1实际已上传成功但检测超时
        if self._is_file_in_upload_list(file_path):
            logger.info("方法2前检测到文件已上传成功，跳过后续上传方式")
            return True

        # 方法2：使用 expect_file_chooser 点击上传区域
        try:
            logger.info("尝试通过 filechooser 上传文件...")
            with self.page.expect_file_chooser(timeout=self.config.upload_timeout * 1000) as fc_info:
                # 在 iframe 中点击上传区域
                clicked = False
                if frame:
                    try:
                        # 尝试多个上传区域文本
                        for upload_text in ["将文件拖到此处", "点击上传", "上传"]:
                            try:
                                upload_area = frame.locator("text=" + upload_text).first
                                upload_area.click(timeout=3000)
                                clicked = True
                                logger.info(f"点击上传区域: {upload_text}")
                                break
                            except Exception:
                                continue
                    except Exception:
                        pass

                if not clicked:
                    # 兜底：点击 .el-upload-dragger 或 .el-upload
                    try:
                        if frame:
                            for selector in [".el-upload-dragger", ".el-upload", ".el-upload__input"]:
                                try:
                                    frame.locator(selector).first.click(timeout=3000)
                                    clicked = True
                                    logger.info(f"点击上传元素: {selector}")
                                    break
                                except Exception:
                                    continue
                        else:
                            self.page.locator(".el-upload-dragger").first.click(timeout=5000)
                            clicked = True
                    except Exception:
                        pass

            fc = fc_info.value
            fc.set_files(str(file_path))
            logger.info("已通过 filechooser 上传文件")
            time.sleep(2)
            if self._wait_upload_complete(file_path):
                return True
            # 方法2等待超时，但文件可能已上传成功
            if self._is_file_in_upload_list(file_path):
                logger.info("filechooser 上传后检测到文件已存在列表中，视为成功")
                return True
        except Exception as e:
            logger.warning(f"方法2上传失败: {e}")

        logger.error("所有上传方式均失败")
        self._save_debug_snapshot("upload_failed")
        return False

    def _find_file_input(self):
        """查找文件上传 input 元素（优先在当前激活 iframe 中）"""
        # 优先在当前激活的 iframe 中查找（上传页面会打开新 iframe）
        frame = self._get_upload_frame()
        if frame:
            try:
                inputs = frame.locator('input[type="file"]').all()
                if inputs:
                    logger.info(f"在 active iframe 中找到 {len(inputs)} 个 input[type=file]")
                    return inputs[0]
            except Exception as e:
                logger.debug(f"active iframe 中查找 input[type=file] 失败: {e}")

        # 兜底：所有 frame
        contexts = [self.page] + list(self.page.frames)
        for context in contexts:
            try:
                inputs = context.locator('input[type="file"]').all()
                if inputs:
                    logger.info(f"在 frame 中找到 {len(inputs)} 个 input[type=file]")
                    return inputs[0]
            except Exception:
                continue

        logger.warning("未找到任何 input[type=file]")
        return None

    def _wait_upload_complete(self, file_path: Optional[Path] = None) -> bool:
        """等待文件上传完成（列表中出现文件名或删除按钮）"""
        deadline = time.time() + self.config.upload_timeout
        file_name = file_path.name if file_path else None

        while time.time() < deadline:
            try:
                # 检查所有 frame（主页面 + 所有 iframe），避免遗漏上传页面
                contexts = [self.page] + list(self.page.frames)
                for context in contexts:
                    try:
                        found = context.evaluate("""(args) => {
                            var fileName = args.fileName;
                            var selectors = [
                                '.el-upload-list__item',
                                '[class*="upload-list"] li',
                                'tr',
                                '.file-list-item',
                                '.upload-file-item',
                                'tbody tr'
                            ];
                            var items = [];
                            selectors.forEach(function(sel) {
                                try { items = items.concat(Array.from(document.querySelectorAll(sel))); } catch(e) {}
                            });
                            for (var i = 0; i < items.length; i++) {
                                var t = (items[i].innerText || items[i].textContent || '').trim();
                                if (t.includes('删除') || t.includes('预览') ||
                                    t.includes('离线账户明细导入') || t.includes('上传成功') ||
                                    t.includes('完成') || t.includes('境内') || t.includes('境外') ||
                                    (fileName && t.includes(fileName))) {
                                    return true;
                                }
                            }
                            // 兜底：页面 body 中是否包含文件名
                            if (fileName) {
                                var bodyText = (document.body && document.body.innerText || '').trim();
                                if (bodyText.includes(fileName)) return true;
                            }
                            return false;
                        }""", {"fileName": file_name})
                        if found:
                            logger.info("文件已出现在上传列表中")
                            return True
                    except Exception:
                        continue
            except Exception:
                pass
            time.sleep(1)

        logger.warning("等待上传完成超时")
        return False

    def _click_recognize(self) -> bool:
        """点击"发起识别"按钮"""
        logger.info("点击发起识别...")

        # 方法1：JS 在 iframe/主页面中查找按钮
        try:
            clicked = self.page.evaluate("""() => {
                var buttons = document.querySelectorAll('button, a, input[type="button"], .el-button');
                for (var j = 0; j < buttons.length; j++) {
                    var btn = buttons[j];
                    var txt = (btn.innerText || btn.textContent || btn.value || '').trim();
                    if (txt === '发起识别' && btn.offsetParent !== null) {
                        btn.click();
                        return {clicked: true, text: txt, tag: btn.tagName};
                    }
                }
                // 包含匹配兜底
                for (var j = 0; j < buttons.length; j++) {
                    var btn = buttons[j];
                    var txt = (btn.innerText || btn.textContent || btn.value || '').trim();
                    if (txt.includes('发起识别') && btn.offsetParent !== null) {
                        btn.click();
                        return {clicked: true, text: txt, tag: btn.tagName, match: 'contains'};
                    }
                }
                return {clicked: false};
            }""")
            if clicked.get("clicked"):
                logger.info(f"JS 点击发起识别成功: {clicked}")
                time.sleep(2)
                return True
        except Exception as e:
            logger.debug(f"JS 点击发起识别失败: {e}")

        # 方法2：Playwright 选择器
        selectors = [
            "button:has-text('发起识别')",
            ".el-button:has-text('发起识别')",
            "text=发起识别",
        ]
        for selector in selectors:
            try:
                btn = self.page.locator(selector).first
                btn.click(timeout=5000)
                logger.info(f"Playwright 点击发起识别成功: {selector}")
                time.sleep(2)
                return True
            except Exception:
                continue

        # 方法3：遍历所有 iframe
        for frame in self.page.frames:
            try:
                clicked = frame.evaluate("""() => {
                    var buttons = document.querySelectorAll('button, a, input[type="button"], .el-button');
                    for (var j = 0; j < buttons.length; j++) {
                        var btn = buttons[j];
                        var txt = (btn.innerText || btn.textContent || btn.value || '').trim();
                        if (txt.includes('发起识别') && btn.offsetParent !== null) {
                            btn.click();
                            return {clicked: true, text: txt};
                        }
                    }
                    return {clicked: false};
                }""")
                if clicked.get("clicked"):
                    logger.info(f"iframe 中点击发起识别成功: {clicked}")
                    time.sleep(2)
                    return True
            except Exception:
                continue

        logger.warning("未找到'发起识别'按钮")
        return False

    def _wait_recognition_success(self, file_path: Path) -> bool:
        """等待识别成功：点击'识别成功'标签页并通过'刷新'按钮轮询状态"""
        file_name = file_path.name
        deadline = time.time() + self.config.recognize_timeout
        interval = getattr(self.config, 'recognize_refresh_interval', 3)

        logger.info(f"开始等待识别成功，超时时间: {self.config.recognize_timeout}秒")

        while time.time() < deadline:
            try:
                # 先切换到'识别成功'标签页
                self._switch_to_recognition_tab("识别成功")
                time.sleep(0.5)  # 等待标签页内容切换

                # 在页面所有 frame 中查找文件名
                if self._is_file_in_any_frame(file_name):
                    logger.info(f"识别成功列表中找到文件: {file_name}")
                    return True

                # 未找到则点击刷新按钮
                self._click_refresh_button()
            except Exception as e:
                logger.debug(f"等待识别成功过程中出错: {e}")

            remaining = deadline - time.time()
            if remaining > 0:
                sleep_time = min(interval, remaining)
                time.sleep(sleep_time)

        logger.warning("等待识别成功超时")
        return False

    def _switch_to_recognition_tab(self, tab_text: str) -> bool:
        """切换到识别结果页面的指定标签页（识别成功/识别中/识别失败/重复文件）"""
        selectors = [
            f"text={tab_text}",
            f".el-tabs__item:has-text('{tab_text}')",
            f".tab-item:has-text('{tab_text}')",
        ]

        # 优先在当前激活 iframe / 所有 iframe 中查找
        contexts = []
        try:
            active_frame = self.page.locator('iframe.active').first
            actual_frame = active_frame.content_frame()
            if actual_frame:
                contexts.append(actual_frame)
        except Exception:
            pass
        contexts.extend([self.page] + list(self.page.frames))

        for context in contexts:
            for selector in selectors:
                try:
                    tab = context.locator(selector).first
                    if tab.is_visible(timeout=2000):
                        tab.click(timeout=3000)
                        logger.debug(f"切换到标签页: {tab_text}")
                        return True
                except Exception:
                    continue

        # 兜底：JS 点击
        for context in contexts:
            try:
                clicked = context.evaluate("""(tabText) => {
                    var all = document.querySelectorAll('*');
                    for (var i = 0; i < all.length; i++) {
                        var el = all[i];
                        var t = (el.innerText || el.textContent || '').trim();
                        if (t === tabText || t.startsWith(tabText)) {
                            var rect = el.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {
                                el.click();
                                return true;
                            }
                        }
                    }
                    return false;
                }""", tab_text)
                if clicked:
                    logger.debug(f"JS 切换到标签页: {tab_text}")
                    return True
            except Exception:
                continue

        return False

    def _is_file_in_any_frame(self, file_name: str) -> bool:
        """在所有 frame 中检查文件名是否存在于页面文本中"""
        contexts = []
        try:
            active_frame = self.page.locator('iframe.active').first
            actual_frame = active_frame.content_frame()
            if actual_frame:
                contexts.append(actual_frame)
        except Exception:
            pass
        contexts.extend([self.page] + list(self.page.frames))

        for context in contexts:
            try:
                found = context.evaluate("""(fileName) => {
                    var bodyText = (document.body && document.body.innerText || '').trim();
                    return bodyText.includes(fileName);
                }""", file_name)
                if found:
                    return True
            except Exception:
                continue
        return False

    def _click_refresh_button(self) -> bool:
        """点击识别结果页面的'刷新'按钮"""
        selectors = [
            "text=刷新",
            "button:has-text('刷新')",
            ".el-button:has-text('刷新')",
            "i.el-icon-refresh",
        ]

        contexts = []
        try:
            active_frame = self.page.locator('iframe.active').first
            actual_frame = active_frame.content_frame()
            if actual_frame:
                contexts.append(actual_frame)
        except Exception:
            pass
        contexts.extend([self.page] + list(self.page.frames))

        for context in contexts:
            for selector in selectors:
                try:
                    btn = context.locator(selector).first
                    if btn.is_visible(timeout=2000):
                        btn.click(timeout=3000)
                        logger.debug("点击刷新按钮")
                        return True
                except Exception:
                    continue

        # 兜底：JS 点击
        for context in contexts:
            try:
                clicked = context.evaluate("""() => {
                    var all = document.querySelectorAll('button, a, .el-button, i');
                    for (var i = 0; i < all.length; i++) {
                        var el = all[i];
                        var t = (el.innerText || el.textContent || '').trim();
                        if (t.includes('刷新')) {
                            var rect = el.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {
                                el.click();
                                return true;
                            }
                        }
                    }
                    return false;
                }""")
                if clicked:
                    logger.debug("JS 点击刷新按钮")
                    return True
            except Exception:
                continue

        return False

    def _click_submit(self) -> bool:
        """点击识别成功后的'提交'按钮，并处理确认弹窗"""
        logger.info("点击提交...")

        # 1. 点击初始提交按钮
        if not self._click_button_by_text("提交"):
            logger.warning("未找到'提交'按钮")
            return False

        time.sleep(1.5)

        # 2. 处理确认提交弹窗
        if self._click_button_by_text("确认提交"):
            logger.info("已点击确认提交")
            time.sleep(2)
            return True

        # 没有确认弹窗也视为成功（可能直接提交）
        logger.info("未检测到确认提交弹窗，视为已提交")
        return True

    def _click_button_by_text(self, text: str, exact: bool = False) -> bool:
        """在页面所有 frame 中查找并点击包含指定文本的按钮"""
        selectors = [
            f"button:has-text('{text}')",
            f".el-button:has-text('{text}')",
            f"text={text}",
        ]

        contexts = []
        try:
            active_frame = self.page.locator('iframe.active').first
            actual_frame = active_frame.content_frame()
            if actual_frame:
                contexts.append(actual_frame)
        except Exception:
            pass
        contexts.extend([self.page] + list(self.page.frames))

        for context in contexts:
            for selector in selectors:
                try:
                    btn = context.locator(selector).first
                    if btn.is_visible(timeout=2000):
                        btn.click(timeout=5000)
                        logger.info(f"点击按钮成功: {text}")
                        return True
                except Exception:
                    continue

        # 兜底：JS 点击
        for context in contexts:
            try:
                clicked = context.evaluate("""(args) => {
                    var text = args.text;
                    var exact = args.exact;
                    var buttons = document.querySelectorAll('button, a, input[type="button"], .el-button, .el-dialog__footer button, [class*="dialog"] button');
                    for (var j = 0; j < buttons.length; j++) {
                        var btn = buttons[j];
                        var txt = (btn.innerText || btn.textContent || btn.value || '').trim();
                        var match = exact ? txt === text : txt.includes(text);
                        if (match && btn.offsetParent !== null) {
                            btn.click();
                            return {clicked: true, text: txt};
                        }
                    }
                    return {clicked: false};
                }""", {"text": text, "exact": exact})
                if clicked.get("clicked"):
                    logger.info(f"JS 点击按钮成功: {clicked}")
                    return True
            except Exception:
                continue

        return False

    def _save_debug_snapshot(self, prefix: str):
        """保存调试截图和 HTML"""
        try:
            debug_dir = Path("debug")
            debug_dir.mkdir(exist_ok=True)
            ts = int(time.time())
            self.page.screenshot(path=str(debug_dir / f"{prefix}_{ts}.png"))
            with open(debug_dir / f"{prefix}_{ts}.html", "w", encoding="utf-8") as f:
                f.write(self.page.content())
            logger.info(f"调试文件已保存: debug/{prefix}_{ts}.*")
        except Exception as e:
            logger.warning(f"保存调试文件失败: {e}")
