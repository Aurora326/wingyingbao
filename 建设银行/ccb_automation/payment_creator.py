# -*- coding: utf-8 -*-
"""
制单模块 - 建设银行企业网银批量模板导入制单

流程：
1. 生成批量转账导入文件（基于模板 template/批量转账模板.xls）
2. 导航：转账业务 → 转账制单 → 批量转账 → 文件录入
3. 上传文件、填写批量名称、选择处理模式
4. 点击"下一步"（save 模式停在此处）
5. 如 action=submit，继续点击提交并处理 U 盾弹窗
"""

import time
import json
import csv
import shutil
import ctypes
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional
from playwright.sync_api import Page
from loguru import logger

# 用于在模态弹窗阻塞 Playwright 时发送系统级键盘事件
try:
    import pyautogui
except Exception:
    pyautogui = None

try:
    import pygetwindow as gw
except Exception:
    gw = None

# Excel 处理（建设银行模板为 .xls，需 xlrd/xlwt）
try:
    import xlrd
    import xlwt
    from xlutils.copy import copy as xlutils_copy
    XL_UTILS_AVAILABLE = True
except Exception:
    XL_UTILS_AVAILABLE = False
    logger.warning("xlrd/xlwt/xlutils 未安装，.xls 模板处理可能不可用")


class PaymentCreator:
    """建设银行批量模板导入制单"""

    DEFAULT_TEMPLATE_MAPPING = {
        "sheet": "Sheet1",
        "start_row": 2,          # 数据从第 2 行开始（1-based，对应 xlwt row 1）
        "clear_until_row": 3002, # 最多支持 3000 笔
        "clear_columns": 15,
        "columns": [
            {"column": 1,  "field": "_seq",          "default": ""},          # 序号（自动生成）
            {"column": 2,  "field": "payer_account",   "default_config": "payer_account"},
            {"column": 3,  "field": "payer_name",      "default_config": "payer_name"},
            {"column": 4,  "field": "payee_bank_type", "default_config": "payee_bank_type", "default": "02"},
            {"column": 5,  "field": "payee_account"},
            {"column": 6,  "field": "payee_name"},
            {"column": 7,  "field": "payee_branch",    "default_config": "payee_branch"},
            {"column": 8,  "field": "payee_bank_code", "default_config": "payee_bank_code"},
            {"column": 9,  "field": "serial_no",       "default_config": "serial_no"},
            {"column": 10, "field": "amount",          "transform": "amount"},
            {"column": 11, "field": "purpose",         "default_config": "purpose"},
            {"column": 12, "field": "remark",          "default_config": "remark"},
            {"column": 13, "field": "notify",          "default_config": "notify", "default": "0"},
            {"column": 14, "field": "payee_phone",     "default_config": "payee_phone"},
            {"column": 15, "field": "notify_msg",      "default_config": "notify_msg"},
        ],
    }

    def __init__(self, page: Page, config: Dict, pin: str = "", cabinet_controller=None):
        self.page = page
        self.config = config
        self.action = config.get('action', 'save')
        self.pin = pin
        self.cabinet_controller = cabinet_controller
        self.items = self._load_items()

    # ==================== 数据加载 ====================

    def _load_items(self) -> List[Dict]:
        """加载付款项"""
        configured_items = self.config.get('items') or []
        if configured_items:
            return [dict(item) for item in configured_items]

        batch_file = (self.config.get('batch_file') or '').strip() or 'temp/batch_payments.json'
        items = self._load_batch_file(batch_file)
        if items:
            return items

        item = {k: v for k, v in self.config.items() if k not in (
            'enabled', 'mode', 'action', 'batch_file', 'template_file',
            'template_mapping_file', 'generated_file', 'items',
            'batch_name', 'verify_name', 'prefer_realtime'
        )}
        if item.get('payer_account') or item.get('amount') or item.get('payee_account'):
            return [item]
        return []

    def _load_batch_file(self, path: str) -> List[Dict]:
        """从批量文件加载"""
        items = []
        batch_path = self._resolve_path(path)
        if not batch_path.exists():
            fallback = self._resolve_path("temp/batch_payments.json")
            if fallback.exists():
                logger.warning(f"批量文件不存在，改用默认付款清单: {batch_path} -> {fallback}")
                batch_path = fallback
        try:
            with open(batch_path, 'r', encoding='utf-8-sig') as f:
                if batch_path.suffix.lower() == '.json':
                    data = json.load(f)
                    items = data if isinstance(data, list) else [data]
                else:
                    reader = csv.DictReader(f)
                    items = list(reader)
        except Exception as e:
            logger.error(f"加载批量文件失败: {batch_path}, {e}")
        return items

    def _resolve_path(self, path: str) -> Path:
        """将配置路径解析为项目内绝对路径。"""
        p = Path(path)
        if p.is_absolute():
            return p
        return Path(__file__).resolve().parent / p

    def _format_amount(self, value: str) -> str:
        try:
            return f"{Decimal(str(value).replace(',', '').strip()):.2f}"
        except (InvalidOperation, ValueError):
            return str(value or "").strip()

    def _amount_decimal(self, value: str) -> Decimal:
        try:
            return Decimal(self._format_amount(value))
        except (InvalidOperation, ValueError):
            return Decimal("0")

    # ==================== 模板映射与文件生成 ====================

    def _load_template_mapping(self) -> Dict[str, Any]:
        mapping_file = self.config.get("template_mapping_file") or "template/payments_batch_template.json"
        mapping_path = self._resolve_path(mapping_file)
        if not mapping_path.exists():
            logger.warning(f"批量模板映射文件不存在，使用内置映射: {mapping_path}")
            return dict(self.DEFAULT_TEMPLATE_MAPPING)
        try:
            with open(mapping_path, "r", encoding="utf-8-sig") as f:
                mapping = json.load(f)
            if not isinstance(mapping, dict) or not mapping.get("columns"):
                raise ValueError("mapping must be an object with columns")
            return mapping
        except Exception as e:
            logger.warning(f"加载批量模板映射失败，使用内置映射: {mapping_path}, {e}")
            return dict(self.DEFAULT_TEMPLATE_MAPPING)

    def _mapped_value(self, item: Dict, column_cfg: Dict[str, Any], row_index: int) -> str:
        fields = column_cfg.get("field", [])
        if isinstance(fields, str):
            fields = [fields]

        value = ""
        for field in fields:
            if field == "_seq":
                value = str(row_index + 1)
                break
            value = item.get(field, "")
            if value not in ("", None):
                break

        default_config = column_cfg.get("default_config")
        if value in ("", None) and default_config:
            value = self.config.get(default_config, "")
        if value in ("", None):
            value = column_cfg.get("default", "")

        transform = column_cfg.get("transform")
        if transform == "amount":
            return self._format_amount(value)
        return str(value or "")

    def _generate_batch_import_file(self) -> Path:
        """按建行批量转账模板生成导入文件。"""
        template_file = self.config.get("template_file") or "template/批量转账模板.xls"
        generated_file = self.config.get("generated_file") or "temp/payment_import.xls"
        template_path = self._resolve_path(template_file)
        output_path = self._resolve_path(generated_file)
        if not template_path.exists():
            raise Exception(f"批量转账模板不存在: {template_path}")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not XL_UTILS_AVAILABLE:
            raise Exception("缺少 xlrd/xlwt/xlutils，无法处理 .xls 模板。请执行: pip install xlrd xlwt xlutils")

        # Windows 下 xlrd 打开文件后会保持句柄，导致 xlwt 无法覆写同一文件。
        # 策略：复制模板到临时文件 → xlrd 读取临时文件 → xlutils.copy 修改 → 保存到目标文件
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".xls", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        shutil.copy2(template_path, tmp_path)

        try:
            # 打开临时副本并修改
            rb = xlrd.open_workbook(str(tmp_path), formatting_info=True)
            wb = xlutils_copy(rb)
            mapping = self._load_template_mapping()
            sheet_name = mapping.get("sheet") or "Sheet1"

            # 找到对应 sheet（xlutils copy 后按索引访问）
            sheet_idx = rb.sheet_names().index(sheet_name) if sheet_name in rb.sheet_names() else 0
            ws = wb.get_sheet(sheet_idx)

            start_row = int(mapping.get("start_row") or 2)          # 1-based
            clear_until_row = int(mapping.get("clear_until_row") or 3002)
            clear_columns = int(mapping.get("clear_columns") or 15)
            columns = mapping.get("columns") or self.DEFAULT_TEMPLATE_MAPPING["columns"]

            # 清空旧数据（保留表头）
            for row in range(start_row, clear_until_row + 1):
                for col in range(1, clear_columns + 1):
                    try:
                        ws.write(row - 1, col - 1, "")
                    except Exception:
                        pass

            total = Decimal("0")
            for offset, item in enumerate(self.items):
                row = start_row + offset  # 1-based Excel row
                amount = self._format_amount(item.get("amount", ""))
                try:
                    total += Decimal(amount)
                except (InvalidOperation, ValueError):
                    pass
                for column_cfg in columns:
                    col = int(column_cfg["column"])  # 1-based
                    val = self._mapped_value(item, column_cfg, offset)
                    ws.write(row - 1, col - 1, val)

            # 保存到目标路径（先关闭 rb 释放句柄）
            rb.release_resources()
            del rb
            wb.save(str(output_path))
        finally:
            # 清理临时文件
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass

        logger.info(f"已生成批量转账导入文件: {output_path}，笔数={len(self.items)}，总金额={total:.2f}")
        return output_path

    # ==================== 页面导航 ====================

    def _navigate_to_batch_payment_page(self):
        """通过菜单导航到批量转账文件录入页面。"""
        logger.info("导航到批量转账制单页面...")
        try:
            self._dismiss_popups()
            # 策略1：尝试直接通过菜单文本定位并点击
            self._click_menu_by_text("转账业务")
            time.sleep(1)
            clicked_file_entry = False
            for text in ("转账制单",):
                clicked = self._click_menu_by_text(text)
                if not clicked:
                    logger.warning(f"未直接点击到菜单: {text}")
                time.sleep(0.8)
                self._dismiss_popups()
            if not self._hover_element_by_text("批量转账"):
                logger.warning("未 hover 到菜单: 批量转账")
            time.sleep(1.0)
            clicked_file_entry = self._click_batch_file_entry()
            if clicked_file_entry:
                time.sleep(3)

            if self._wait_for_batch_import_page(timeout=25 if clicked_file_entry else 8):
                logger.info("已成功进入批量转账文件录入页面")
                return

            # 策略2：如果上述方式未成功，尝试直接通过菜单 hover 链
            logger.info("尝试通过 hover 菜单链导航...")
            clicked_file_entry = self._hover_menu_chain()
            time.sleep(3)

            if self._wait_for_batch_import_page(timeout=25 if clicked_file_entry else 8):
                logger.info("已成功进入批量转账文件录入页面")
                return

            raise Exception("未能进入批量转账文件录入页面")
        except Exception as e:
            logger.error(f"批量转账页面导航失败: {e}")
            self._save_debug_snapshot()
            raise

    def _click_menu_by_text(self, text: str) -> bool:
        """尝试点击包含指定文本的菜单项。"""
        for label, context in self._iter_contexts():
            try:
                result = self._interact_text_in_context(context, text, action="click", exact=True)
                if result and result.get("clicked"):
                    logger.info(f"已点击菜单: {text} ({label}, {result.get('tag')})")
                    return True
            except Exception:
                continue

        selectors = [f'a:has-text("{text}")', f'span:has-text("{text}")', f'li:has-text("{text}")']
        for selector in selectors:
            try:
                loc = self.page.locator(selector).first
                if loc.count() > 0 and loc.is_visible(timeout=2000):
                    loc.click(timeout=3000)
                    logger.info(f"已点击菜单: {text}")
                    return True
            except Exception:
                continue
        return False

    def _hover_menu_chain(self):
        """通过 hover 多级菜单导航到文件录入。"""
        try:
            for text in ("转账业务", "转账制单", "批量转账"):
                self._hover_element_by_text(text)
                time.sleep(0.6)
                if text != "批量转账":
                    self._click_menu_by_text(text)
                    time.sleep(0.6)
            return self._click_batch_file_entry()
        except Exception as e:
            logger.warning(f"hover 菜单链失败: {e}")
            return False

    def _click_batch_file_entry(self) -> bool:
        """点击“批量转账”右侧展开的“文件录入”，避免误点首页常用功能卡片。"""
        self._hover_element_by_text("批量转账")
        time.sleep(0.8)
        for label, context in self._iter_contexts():
            try:
                result = context.evaluate("""
                    () => {
                        const norm = (t) => (t || '').replace(/\\s+/g, '').trim();
                        const isVisible = (el) => {
                            const rect = el.getBoundingClientRect();
                            const style = window.getComputedStyle(el);
                            return rect.width > 0 && rect.height > 0
                                && style.display !== 'none'
                                && style.visibility !== 'hidden';
                        };
                        const clickable = (el) => {
                            const selfClickable = el.matches && el.matches('a, button, input[type="button"], input[type="submit"], [onclick], [ng-click], [data-ng-click]');
                            if (selfClickable) return el;
                            const childClickable = el.querySelector && el.querySelector('a, button, input[type="button"], input[type="submit"], [onclick], [ng-click], [data-ng-click]');
                            if (childClickable) return childClickable;
                            return el.closest('a, button, input[type="button"], input[type="submit"], li, [onclick], [ng-click], [data-ng-click]') || el;
                        };
                        const mouse = (el, events) => {
                            const rect = el.getBoundingClientRect();
                            const init = {
                                bubbles: true,
                                cancelable: true,
                                view: window,
                                clientX: rect.left + rect.width / 2,
                                clientY: rect.top + rect.height / 2
                            };
                            for (const evt of events) el.dispatchEvent(new MouseEvent(evt, init));
                        };

                        const all = Array.from(document.querySelectorAll('a, button, input[type="button"], input[type="submit"], span, li, div'));
                        const batchItems = all
                            .filter(el => isVisible(el) && norm(el.tagName === 'INPUT' ? el.value : el.textContent) === '批量转账')
                            .map(el => {
                                const target = clickable(el);
                                const rect = target.getBoundingClientRect();
                                return {el, target, rect, area: rect.width * rect.height};
                            })
                            .sort((a, b) => a.area - b.area);

                        const batch = batchItems[0];
                        if (!batch) return {clicked: false, reason: '未找到批量转账', candidates: []};

                        batch.target.scrollIntoView({block: 'center', inline: 'center'});
                        mouse(batch.target, ['mouseover', 'mouseenter', 'mousemove']);
                        if (batch.target.parentElement) mouse(batch.target.parentElement, ['mouseover', 'mouseenter', 'mousemove']);

                        const b = batch.target.getBoundingClientRect();
                        const currentAll = Array.from(document.querySelectorAll('a, button, input[type="button"], input[type="submit"], span, li, div'));
                        const fileItems = currentAll
                            .filter(el => isVisible(el) && norm(el.tagName === 'INPUT' ? el.value : el.textContent) === '文件录入')
                            .map(el => {
                                const target = clickable(el);
                                const rect = target.getBoundingClientRect();
                                return {
                                    el,
                                    target,
                                    rect,
                                    text: (el.textContent || el.value || '').replace(/\\s+/g, ' ').trim(),
                                    dx: rect.left - b.right,
                                    dy: Math.abs((rect.top + rect.height / 2) - (b.top + b.height / 2)),
                                    area: rect.width * rect.height
                                };
                            });

                        const submenuItems = fileItems
                            .filter(item => item.rect.left >= b.left - 5 && item.rect.top <= b.bottom + 120 && item.rect.bottom >= b.top - 120)
                            .sort((a, b) => (a.dy - b.dy) || (Math.abs(a.dx) - Math.abs(b.dx)) || (a.area - b.area));

                        const hit = submenuItems[0];
                        if (!hit) {
                            return {
                                clicked: false,
                                reason: '未找到批量转账旁的文件录入',
                                batchRect: {left: b.left, top: b.top, right: b.right, bottom: b.bottom},
                                fileCandidates: fileItems.slice(0, 10).map(i => ({
                                    left: i.rect.left, top: i.rect.top, right: i.rect.right, bottom: i.rect.bottom, text: i.text, dx: i.dx, dy: i.dy
                                }))
                            };
                        }

                        const clickTarget = clickable(hit.el);
                        clickTarget.scrollIntoView({block: 'center', inline: 'center'});
                        mouse(clickTarget, ['mouseover', 'mouseenter', 'mousemove', 'mousedown', 'mouseup', 'click']);
                        try { clickTarget.click(); } catch (e) {}
                        if (clickTarget !== hit.el) {
                            try { hit.el.click(); } catch (e) {}
                        }
                        if (clickTarget.parentElement) {
                            try { clickTarget.parentElement.click(); } catch (e) {}
                        }
                        return {
                            clicked: true,
                            tag: clickTarget.tagName,
                            className: clickTarget.className || '',
                            href: clickTarget.href || clickTarget.getAttribute('href') || '',
                            onclick: clickTarget.getAttribute('onclick') || '',
                            batchRect: {left: b.left, top: b.top, right: b.right, bottom: b.bottom},
                            fileRect: {left: hit.rect.left, top: hit.rect.top, right: hit.rect.right, bottom: hit.rect.bottom}
                        };
                    }
                """)
                if result and result.get("clicked"):
                    logger.info(f"已点击批量转账子菜单文件录入 ({label}): {result}")
                    return True
                logger.info(f"批量转账子菜单文件录入未命中 ({label}): {result}")
            except Exception as e:
                logger.debug(f"点击批量转账子菜单文件录入失败 ({label}): {e}")
        return False

    def _coordinate_menu_chain(self):
        """建行菜单脚本事件失效时，按视频中的菜单相对位置点击。"""
        try:
            viewport = self.page.viewport_size or {"width": 1920, "height": 1080}

            def click_ratio(rx, ry, delay=0.7):
                x = viewport["width"] * rx
                y = viewport["height"] * ry
                self.page.mouse.move(x, y)
                time.sleep(0.15)
                self.page.mouse.click(x, y)
                time.sleep(delay)

            def hover_ratio(rx, ry, delay=0.5):
                self.page.mouse.move(viewport["width"] * rx, viewport["height"] * ry)
                time.sleep(delay)

            logger.info(f"坐标兜底导航，viewport={viewport}")
            # 基于失败截图的相对位置：顶栏“转账业务” -> 左侧“转账制单” -> “批量转账” -> 右侧“文件录入”。
            click_ratio(0.278, 0.113)   # 顶栏：转账业务
            click_ratio(0.148, 0.262)   # 下拉：转账制单
            hover_ratio(0.148, 0.395)   # 左侧：批量转账，先悬停展开右侧子菜单
            click_ratio(0.148, 0.395, delay=0.2)
            hover_ratio(0.238, 0.395, delay=0.2)
            click_ratio(0.238, 0.395, delay=2.5)  # 右侧：文件录入
        except Exception as e:
            logger.warning(f"坐标菜单链失败: {e}")

    def _iter_contexts(self):
        """遍历主页面和 iframe，建行旧页面会在不同容器中切换内容。"""
        yield "page", self.page
        for i, frame in enumerate(self.page.frames):
            yield f"frame[{i}]", frame

    @staticmethod
    def _click_text_in_context(context, text: str, exact: bool = True) -> Dict[str, Any]:
        """在一个页面上下文里点击最小的可见文本元素。"""
        return PaymentCreator._interact_text_in_context(context, text, action="click", exact=exact)

    @staticmethod
    def _interact_text_in_context(context, text: str, action: str = "click", exact: bool = True) -> Dict[str, Any]:
        """在一个页面上下文里对最小的可见文本元素触发 hover/click。"""
        return context.evaluate("""
            (args) => {
                const text = args.text;
                const exact = args.exact;
                const action = args.action;
                const norm = (t) => (t || '').replace(/\\s+/g, '').trim();
                const target = norm(text);
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0
                        && style.display !== 'none'
                        && style.visibility !== 'hidden';
                };
                const candidates = [];
                for (const el of document.querySelectorAll('a, button, input[type="button"], input[type="submit"], span, li, div')) {
                    if (!isVisible(el)) continue;
                    const raw = el.tagName === 'INPUT' ? el.value : el.textContent;
                    const value = norm(raw);
                    if (!value) continue;
                    const matched = exact ? value === target : value.includes(target);
                    if (!matched) continue;
                    const rect = el.getBoundingClientRect();
                    candidates.push({
                        el,
                        tag: el.tagName,
                        text: (raw || '').replace(/\\s+/g, ' ').trim(),
                        area: rect.width * rect.height
                    });
                }
                candidates.sort((a, b) => a.area - b.area);
                const hit = candidates[0];
                if (!hit) return {clicked: false, hovered: false, reason: 'not found'};
                const el = hit.el.closest('a, button, input[type="button"], input[type="submit"], li, [onclick], [ng-click], [data-ng-click]')
                    || hit.el;
                el.scrollIntoView({block: 'center', inline: 'center'});
                const rect = el.getBoundingClientRect();
                const eventInit = {
                    bubbles: true,
                    cancelable: true,
                    view: window,
                    clientX: rect.left + rect.width / 2,
                    clientY: rect.top + rect.height / 2
                };
                for (const evt of ['mouseover', 'mouseenter', 'mousemove']) {
                    el.dispatchEvent(new MouseEvent(evt, eventInit));
                    if (el.parentElement) el.parentElement.dispatchEvent(new MouseEvent(evt, eventInit));
                }
                if (action === 'hover') {
                    return {hovered: true, clicked: false, tag: hit.tag, text: hit.text};
                }
                for (const evt of ['mousedown', 'mouseup', 'click']) {
                    el.dispatchEvent(new MouseEvent(evt, eventInit));
                }
                return {clicked: true, hovered: true, tag: el.tagName, text: hit.text};
            }
        """, {"text": text, "exact": exact, "action": action})

    def _hover_element_by_text(self, text: str) -> bool:
        """hover 包含指定文本的元素。"""
        selectors = [
            f'a:has-text("{text}")',
            f'span:has-text("{text}")',
            f'li:has-text("{text}")',
        ]
        for ctx_label, context in self._iter_contexts():
            for selector in selectors:
                try:
                    loc = context.locator(selector).first
                    if loc.count() > 0 and loc.is_visible(timeout=1500):
                        loc.hover(timeout=3000)
                        logger.info(f"已真实 hover 菜单: {text} ({ctx_label}, {selector})")
                        return True
                except Exception:
                    continue

        for label, context in self._iter_contexts():
            try:
                result = self._interact_text_in_context(context, text, action="hover", exact=True)
                if result and result.get("hovered"):
                    logger.info(f"已 hover 菜单: {text} ({label}, {result.get('tag')})")
                    return True
            except Exception:
                continue

        for selector in selectors:
            try:
                loc = self.page.locator(selector).first
                if loc.count() > 0 and loc.is_visible(timeout=2000):
                    loc.hover(timeout=3000)
                    return True
            except Exception:
                continue
        return False

    def _is_batch_import_page(self) -> bool:
        """检查当前是否在批量文件导入页面。"""
        for label, context in self._iter_contexts():
            try:
                state = context.evaluate("""
                    () => {
                        const body = (document.body && document.body.innerText || '').replace(/\\s+/g, '');
                        const hasFileInput = document.querySelectorAll('input[type="file"]').length > 0;
                        const hasBatchFileLabel = body.includes('批量转账单据文件')
                            || body.includes('批量文件导入')
                            || body.includes('批量转账单据');
                        const hasFormControls = body.includes('批量名称')
                            && (body.includes('下一步') || body.includes('浏览') || body.includes('模板下载'));
                        return {hasFileInput, hasBatchFileLabel, hasFormControls, text: body.slice(0, 200)};
                    }
                """)
                if state and (state.get("hasFileInput") or (state.get("hasBatchFileLabel") and state.get("hasFormControls"))):
                    logger.info(f"批量转账文件录入页检测通过 ({label}): {state}")
                    return True
            except Exception:
                continue
        return False

    def _wait_for_batch_import_page(self, timeout: int = 8) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._is_batch_import_page():
                return True
            time.sleep(0.5)
        return False

    # ==================== 文件上传与表单填写 ====================

    def _upload_batch_file(self, import_file: Path):
        """上传批量转账文件。"""
        logger.info(f"上传批量转账文件: {import_file}")

        # 尝试在当前页面或 iframe 中找到文件上传控件
        file_input = self._find_file_input()
        if not file_input:
            self._log_upload_page_state()
            self._save_debug_snapshot()
            raise Exception("未找到批量转账文件上传控件")

        file_input.set_input_files(str(import_file))
        logger.info("文件已设置到上传控件")
        time.sleep(2)

    def _find_file_input(self):
        """在当前页面和所有 iframe 中查找真正的文件上传 input（type='file'）。"""
        contexts = [("page", self.page)] + [
            (f"frame[{i}]", frame) for i, frame in enumerate(self.page.frames)
        ]
        for label, context in contexts:
            try:
                # 策略1：通过 JS 精确查找可见的 type='file' input（排除 hidden）
                result = context.evaluate("""
                    () => {
                        const inputs = document.querySelectorAll('input[type="file"]');
                        for (const inp of inputs) {
                            if (inp.type !== 'file') continue;
                            const style = window.getComputedStyle(inp);
                            if (style.display === 'none' || style.visibility === 'hidden') continue;
                            const rect = inp.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {
                                return {
                                    found: true,
                                    selector: 'input[type="file"]',
                                    name: inp.name || '',
                                    id: inp.id || '',
                                    className: inp.className || ''
                                };
                            }
                        }
                        return {found: false};
                    }
                """)
                if result and result.get("found"):
                    sel = result.get("selector", "input[type='file']")
                    # 如果有 name/id，构建更精确的选择器
                    if result.get("name"):
                        sel = f"input[type='file'][name='{result['name']}']"
                    elif result.get("id"):
                        sel = f"input[type='file']#{result['id']}"
                    loc = context.locator(sel).first
                    if loc.count() > 0:
                        logger.info(f"在 {label} 找到文件上传控件: {sel}")
                        return loc

                # 策略2：兜底，通过通用选择器查找，但额外检查 type
                for selector in ["input[type='file']"]:
                    loc = context.locator(selector).first
                    if loc.count() > 0:
                        try:
                            if loc.is_visible(timeout=2000) or loc.is_enabled(timeout=2000):
                                logger.info(f"在 {label} 找到文件上传控件: {selector}")
                                return loc
                        except Exception:
                            # 不可见但确实是 file input，仍尝试使用
                            logger.info(f"在 {label} 找到文件上传控件(不可见): {selector}")
                            return loc
            except Exception:
                continue
        return None

    def _log_upload_page_state(self):
        """上传控件定位失败时记录当前页面线索。"""
        for label, context in self._iter_contexts():
            try:
                state = context.evaluate("""
                    () => {
                        const body = (document.body && document.body.innerText || '').replace(/\\s+/g, ' ').trim();
                        return {
                            url: location.href,
                            title: document.title,
                            fileInputs: document.querySelectorAll('input[type="file"]').length,
                            inputs: Array.from(document.querySelectorAll('input')).slice(0, 20).map(i => ({
                                type: i.type || '',
                                id: i.id || '',
                                name: i.name || '',
                                value: i.type === 'password' ? '' : (i.value || ''),
                                className: i.className || ''
                            })),
                            text: body.slice(0, 500)
                        };
                    }
                """)
                logger.info(f"上传页状态 ({label}): {state}")
            except Exception as e:
                logger.debug(f"读取上传页状态失败 ({label}): {e}")

    def _fill_batch_form(self):
        """填写批量转账表单（批量名称、处理模式等）。"""
        # 批量名称
        batch_name = self.config.get("batch_name", "")
        if not batch_name and self.items:
            # 默认使用日期作为批量名称
            from datetime import datetime
            batch_name = f"批量转账_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        if batch_name:
            self._fill_input_by_label("批量名称", batch_name)
            time.sleep(0.5)

        # 处理模式：行内收款账户户名校验
        if self.config.get("verify_name", False):
            self._check_checkbox_by_label("行内收款账户户名校验")
            time.sleep(0.3)

        # 处理模式：优先采用跨行实时转账
        if self.config.get("prefer_realtime", False):
            self._check_checkbox_by_label("优先采用跨行实时转账")
            time.sleep(0.3)

    def _fill_input_by_label(self, label: str, value: str) -> bool:
        """通过 label 文本找到对应的 input 并填写。"""
        if not value:
            return True
        for ctx_label, context in self._iter_contexts():
            try:
                result = context.evaluate("""
                    (args) => {
                        const labelText = args.label;
                        const value = args.value;
                        const norm = (t) => t.replace(/[：:*\\s]/g, '').trim();
                        const target = norm(labelText);

                        // 1. 找 label 元素
                        let labelEl = null;
                        for (const el of document.querySelectorAll('label, span, div, td, th')) {
                            if (norm(el.textContent) === target) {
                                labelEl = el;
                                break;
                            }
                        }
                        if (!labelEl) return {success: false, reason: '未找到label'};

                        // 2. 找关联 input
                        let inputEl = null;
                        if (labelEl.htmlFor) inputEl = document.getElementById(labelEl.htmlFor);
                        if (!inputEl) {
                            const container = labelEl.closest('.form-group, .form-item, li, .row, td, div, tr');
                            if (container) inputEl = container.querySelector('input:not([type="hidden"]), textarea');
                        }
                        if (!inputEl) {
                            let sibling = labelEl.nextElementSibling;
                            while (sibling && !inputEl) {
                                inputEl = sibling.querySelector('input, textarea');
                                if (!inputEl && (sibling.tagName === 'INPUT' || sibling.tagName === 'TEXTAREA')) {
                                    inputEl = sibling;
                                }
                                sibling = sibling.nextElementSibling;
                            }
                        }
                        if (!inputEl) return {success: false, reason: '未找到input'};
                        if (inputEl.disabled || inputEl.readOnly || inputEl.type === 'hidden') {
                            return {success: false, reason: 'input不可用'};
                        }

                        inputEl.focus();
                        inputEl.value = value;
                        inputEl.setAttribute('value', value);
                        inputEl.dispatchEvent(new Event('input', {bubbles: true}));
                        inputEl.dispatchEvent(new Event('change', {bubbles: true}));
                        inputEl.dispatchEvent(new Event('blur', {bubbles: true}));
                        return {success: true};
                    }
                """, {"label": label, "value": value})
                if result and result.get("success"):
                    logger.info(f"已通过 JS 填写 [{label}]: {value} ({ctx_label})")
                    return True
            except Exception as e:
                logger.debug(f"JS 填写 [{label}] 失败 ({ctx_label}): {e}")

        # 兜底：通过 placeholder 或邻近搜索
        for ctx_label, context in self._iter_contexts():
            try:
                loc = context.locator(f"input[placeholder*='{label}']").first
                if loc.count() > 0 and loc.is_visible(timeout=1500):
                    loc.fill(value)
                    logger.info(f"已通过 placeholder 填写 [{label}]: {value} ({ctx_label})")
                    return True
            except Exception:
                pass

        logger.warning(f"填写 [{label}] 失败")
        return False

    def _check_checkbox_by_label(self, label: str) -> bool:
        """通过 label 文本找到对应的 checkbox 并勾选。"""
        for ctx_label, context in self._iter_contexts():
            try:
                result = context.evaluate("""
                    (args) => {
                        const labelText = args.label;
                        const norm = (t) => (t || '').replace(/[：:*\\s]/g, '').trim();
                        const target = norm(labelText);

                        // 1. 找包含该文本的元素
                        let container = null;
                        for (const el of document.querySelectorAll('label, span, div, td, th')) {
                            if (norm(el.textContent) === target || norm(el.textContent).includes(target)) {
                                container = el.closest('label') || el;
                                break;
                            }
                        }
                        if (!container) return {success: false, reason: '未找到文本'};

                        // 2. 找 checkbox
                        let checkbox = container.querySelector('input[type="checkbox"]');
                        if (!checkbox && container.tagName === 'LABEL' && container.htmlFor) {
                            checkbox = document.getElementById(container.htmlFor);
                        }
                        if (!checkbox) {
                            // 在父元素中找
                            const parent = container.closest('.form-group, .form-item, li, .row, div, tr, td');
                            if (parent) checkbox = parent.querySelector('input[type="checkbox"]');
                        }
                        if (!checkbox) return {success: false, reason: '未找到checkbox'};

                        if (!checkbox.checked) {
                            checkbox.click();
                            checkbox.checked = true;
                            checkbox.dispatchEvent(new Event('change', {bubbles: true}));
                        }
                        return {success: true, checked: checkbox.checked};
                    }
                """, {"label": label})
                if result and result.get("success"):
                    logger.info(f"已勾选 [{label}] ({ctx_label})")
                    return True
            except Exception as e:
                logger.debug(f"勾选 [{label}] 失败 ({ctx_label}): {e}")
        return False

    # ==================== 提交与 U 盾 ====================

    def _click_next_step(self) -> bool:
        """点击"下一步"按钮。"""
        logger.info("点击下一步...")
        for ctx_label, context in self._iter_contexts():
            try:
                btn = context.locator('button:has-text("下一步"), input[value="下一步"], a:has-text("下一步")').first
                if btn.count() > 0 and btn.is_visible(timeout=3000):
                    btn.click(timeout=5000)
                    logger.info(f"已点击下一步 ({ctx_label})")
                    time.sleep(3)
                    return True
            except Exception as e:
                logger.warning(f"Playwright 点击下一步失败 ({ctx_label}): {e}")

        # JS 兜底
        for ctx_label, context in self._iter_contexts():
            try:
                result = context.evaluate("""
                    () => {
                        const btns = document.querySelectorAll('button, input[type="button"], input[type="submit"], a');
                        for (const btn of btns) {
                            const txt = (btn.textContent || btn.value || '').trim();
                            if (txt === '下一步') {
                                btn.click();
                                return {clicked: true};
                            }
                        }
                        return {clicked: false};
                    }
                """)
                if result and result.get("clicked"):
                    logger.info(f"已通过 JS 点击下一步 ({ctx_label})")
                    time.sleep(3)
                    return True
            except Exception as e:
                logger.warning(f"JS 点击下一步失败 ({ctx_label}): {e}")
        return False

    def _click_submit(self) -> bool:
        """点击"提交"按钮（下一步之后的页面可能出现）。"""
        logger.info("查找提交按钮...")
        for text in ["提交", "确定", "确认"]:
            for ctx_label, context in self._iter_contexts():
                try:
                    btn = context.locator(f'button:has-text("{text}"), input[value="{text}"], a:has-text("{text}")').first
                    if btn.count() > 0 and btn.is_visible(timeout=2000):
                        btn.click(timeout=5000)
                        logger.info(f"已点击 {text} ({ctx_label})")
                        time.sleep(3)
                        return True
                except Exception:
                    continue
        return False

    def _select_uploaded_success_rows(self) -> bool:
        """上传成功记录页勾选成功记录或全选。"""
        for ctx_label, context in self._iter_contexts():
            try:
                result = context.evaluate("""
                    () => {
                        const body = (document.body && document.body.innerText || '').replace(/\\s+/g, '');
                        if (!body.includes('上传成功记录') && !body.includes('上传失败记录')) {
                            return {matched: false};
                        }
                        const visible = (el) => {
                            const rect = el.getBoundingClientRect();
                            const style = window.getComputedStyle(el);
                            return rect.width > 0 && rect.height > 0
                                && style.display !== 'none'
                                && style.visibility !== 'hidden';
                        };
                        const checkboxes = Array.from(document.querySelectorAll('input[type="checkbox"]'))
                            .filter(visible);
                        let clicked = 0;
                        for (const cb of checkboxes) {
                            if (!cb.checked) {
                                cb.click();
                                cb.checked = true;
                                cb.dispatchEvent(new Event('change', {bubbles: true}));
                                clicked += 1;
                            }
                        }
                        return {matched: true, total: checkboxes.length, clicked};
                    }
                """)
                if result and result.get("matched"):
                    logger.info(f"上传成功记录勾选结果 ({ctx_label}): {result}")
                    return int(result.get("total") or 0) > 0
            except Exception as e:
                logger.debug(f"勾选上传成功记录失败 ({ctx_label}): {e}")
        logger.warning("未检测到上传成功记录页，跳过全选")
        return False

    def _fill_transaction_password_and_confirm(self) -> bool:
        """复核提交页输入交易密码并点击确认。"""
        if not self.pin:
            logger.warning("未配置 PIN/交易密码，跳过交易密码自动输入")
            return False
        for ctx_label, context in self._iter_contexts():
            try:
                result = context.evaluate("""
                    (password) => {
                        const body = (document.body && document.body.innerText || '').replace(/\\s+/g, '');
                        if (!body.includes('交易密码') && !body.includes('认证信息')) {
                            return {matched: false};
                        }
                        const visible = (el) => {
                            const rect = el.getBoundingClientRect();
                            const style = window.getComputedStyle(el);
                            return rect.width > 0 && rect.height > 0
                                && style.display !== 'none'
                                && style.visibility !== 'hidden';
                        };
                        let input = Array.from(document.querySelectorAll('input[type="password"], input'))
                            .find(el => visible(el) && (el.type === 'password' || /pwd|pass|密码/i.test(`${el.id} ${el.name}`)));
                        if (!input) {
                            const labels = Array.from(document.querySelectorAll('td, th, label, span, div'))
                                .filter(el => visible(el) && (el.textContent || '').includes('交易密码'));
                            for (const label of labels) {
                                const row = label.closest('tr, div, table') || label.parentElement;
                                if (row) {
                                    input = Array.from(row.querySelectorAll('input')).find(visible);
                                    if (input) break;
                                }
                            }
                        }
                        if (!input) return {matched: true, filled: false, reason: '未找到交易密码输入框'};
                        input.focus();
                        input.value = password;
                        input.setAttribute('value', password);
                        input.dispatchEvent(new Event('input', {bubbles: true}));
                        input.dispatchEvent(new Event('change', {bubbles: true}));
                        input.dispatchEvent(new Event('blur', {bubbles: true}));
                        return {matched: true, filled: true, type: input.type, id: input.id || '', name: input.name || ''};
                    }
                """, self.pin)
                if result and result.get("matched"):
                    logger.info(f"交易密码填写结果 ({ctx_label}): {result}")
                    if not result.get("filled"):
                        return False
                    time.sleep(0.5)
                    return self._click_button_by_texts(["确认"], preferred_context=context, preferred_label=ctx_label)
            except Exception as e:
                logger.debug(f"填写交易密码失败 ({ctx_label}): {e}")
        logger.info("未检测到交易密码复核页")
        return False

    def _click_button_by_texts(self, texts: List[str], preferred_context=None, preferred_label: str = "") -> bool:
        contexts = [(preferred_label or "preferred", preferred_context)] if preferred_context else []
        contexts += list(self._iter_contexts())
        seen = set()
        for ctx_label, context in contexts:
            if not context or id(context) in seen:
                continue
            seen.add(id(context))
            for text in texts:
                try:
                    btn = context.locator(f'button:has-text("{text}"), input[value="{text}"], a:has-text("{text}")').first
                    if btn.count() > 0 and btn.is_visible(timeout=1500):
                        btn.click(timeout=5000)
                        logger.info(f"已点击按钮 {text} ({ctx_label})")
                        time.sleep(3)
                        return True
                except Exception:
                    pass
                try:
                    result = context.evaluate("""
                        (text) => {
                            const visible = (el) => {
                                const rect = el.getBoundingClientRect();
                                const style = window.getComputedStyle(el);
                                return rect.width > 0 && rect.height > 0
                                    && style.display !== 'none'
                                    && style.visibility !== 'hidden';
                            };
                            for (const el of document.querySelectorAll('button, input[type="button"], input[type="submit"], a')) {
                                if (!visible(el)) continue;
                                const val = (el.textContent || el.value || '').trim();
                                if (val === text) {
                                    el.click();
                                    return {clicked: true, tag: el.tagName};
                                }
                            }
                            return {clicked: false};
                        }
                    """, text)
                    if result and result.get("clicked"):
                        logger.info(f"已通过 JS 点击按钮 {text} ({ctx_label})")
                        time.sleep(3)
                        return True
                except Exception:
                    pass
        return False

    def _finish_batch_action(self) -> str:
        """完成批量操作。"""
        if self.action == 'submit':
            if self._click_next_step():
                time.sleep(2)
                # 上传成功记录页：先全选成功记录，再进入复核页
                if self._select_uploaded_success_rows():
                    if not self._click_next_step():
                        return "上传成功记录页下一步点击失败"
                    time.sleep(2)
                # 复核页：输入交易密码并确认
                self._fill_transaction_password_and_confirm()
                self._handle_confirm_dialog()
                self._handle_ukey_password_dialog()
                self._handle_ukey_physical_confirm()
                self._handle_confirm_dialog()
                result = self._handle_result_dialog()
                return result or "提交成功"
            return "下一步点击失败"
        else:
            logger.info("save 模式：已停在下一步前等待人工核对")
            return "批量导入准备完成，已停在下一步前"

    def create_all(self) -> Dict[str, str]:
        """通过批量模板导入创建付款单。"""
        results = {}
        if not self.items:
            logger.warning("没有配置付款项")
            return results

        try:
            import_file = self._generate_batch_import_file()
            self._navigate_to_batch_payment_page()
            self._dismiss_popups()
            self._upload_batch_file(import_file)
            self._fill_batch_form()
            self._save_debug_snapshot()
            result = self._finish_batch_action()
            results["batch_import"] = result
        except Exception as e:
            logger.error(f"批量模板导入制单失败: {e}")
            results["batch_import"] = f"失败: {e}"
        return results

    # ==================== 弹窗与 U 盾处理 ====================

    def _handle_confirm_dialog(self):
        """处理确认对话框"""
        for ctx_label, context in self._iter_contexts():
            try:
                for text in ['确定', '确认', '是']:
                    btn = context.locator(f"button:has-text('{text}'), input[value='{text}']").first
                    if btn.count() > 0 and btn.is_visible(timeout=2000):
                        btn.click()
                        logger.info(f"已处理确认弹窗: {text} ({ctx_label})")
                        time.sleep(1)
                        return
            except Exception:
                pass

    def _handle_result_dialog(self) -> Optional[str]:
        """处理结果弹窗"""
        dialog_selectors = [
            ".dialog-body",
            ".modal-body",
            ".el-message-box__message",
            "[role='dialog']",
            ".layui-layer-content",
        ]
        for ctx_label, context in self._iter_contexts():
            for selector in dialog_selectors:
                try:
                    el = context.locator(selector).first
                    if el.count() > 0 and el.is_visible(timeout=3000):
                        text = el.text_content() or ""
                        logger.info(f"弹窗内容 ({ctx_label}): {text.strip()}")
                        self._save_result_screenshot()
                        close_btn = context.locator(
                            "button:has-text('关闭'), button:has-text('确定'), input[value='确定']"
                        ).first
                        if close_btn.count() > 0 and close_btn.is_visible(timeout=2000):
                            close_btn.click()
                            time.sleep(1)
                        return text.strip()
                except Exception:
                    continue
        return None

    def _handle_ukey_password_dialog(self):
        """处理交易签名时的 U 盾密码弹窗。"""
        if not self.pin:
            logger.info("未配置 PIN，跳过 U 盾密码弹窗自动输入")
            return
        try:
            logger.info("等待 U 盾密码弹窗出现...")
            win = self._wait_for_ukey_window(timeout=20)
            if win:
                self._activate_ukey_window(win)
            else:
                logger.warning("未识别到 U 盾密码窗口，按当前焦点尝试输入")

            self._click_ukey_password_input(win)
            if self._input_with_aikeyboard(self.pin):
                logger.info("AiKeyboard U 盾密码输入完成")
            else:
                logger.warning("AiKeyboard 输入失败")

            time.sleep(0.5)
            self._confirm_ukey_window(win)
            time.sleep(5)
        except Exception as e:
            logger.error(f"处理 U 盾密码弹窗失败: {e}")

    def _handle_ukey_physical_confirm(self):
        """确认完 U 盾密码后，调用机柜控制器按压物理 OK 键。"""
        if not self.cabinet_controller:
            return
        try:
            logger.info("U 盾密码已提交，等待 5 秒后调用机柜按压物理 OK 键...")
            time.sleep(5)
            for attempt in range(3):
                try:
                    if self.cabinet_controller.click_ok():
                        logger.info(f"机柜按压 OK 键成功（第 {attempt + 1} 次），等待 10 秒...")
                        time.sleep(10)
                        return
                    else:
                        logger.warning(f"机柜按压 OK 键返回失败（第 {attempt + 1} 次）")
                except Exception as e:
                    logger.warning(f"机柜按压 OK 键异常（第 {attempt + 1} 次）: {e}")
                time.sleep(1)
            logger.error("机柜按压 OK 键多次失败，请手动按 U 盾 OK 键")
        except Exception as e:
            logger.debug(f"调用机柜按压 OK 键失败: {e}")

    def _wait_for_ukey_window(self, timeout: int = 20):
        if not gw:
            logger.warning("pygetwindow 不可用，无法按标题识别 U 盾密码窗口")
            time.sleep(3)
            return None
        deadline = time.time() + timeout
        keywords = ["校验U盾密码", "U盾密码", "网银盾密码", "U盾"]
        while time.time() < deadline:
            try:
                for win in gw.getAllWindows():
                    title = (win.title or "").strip()
                    if not title or "Chrome" in title or "Google Chrome" in title:
                        continue
                    if title and any(k in title for k in keywords):
                        logger.info(f"检测到 U 盾密码窗口: title={title}")
                        return win
            except Exception:
                pass
            time.sleep(0.5)
        return None

    def _activate_ukey_window(self, win):
        if not win:
            return
        try:
            if win.isMinimized:
                win.restore()
            win.activate()
            time.sleep(0.4)
        except Exception:
            pass

    def _click_ukey_password_input(self, win):
        if not pyautogui:
            return
        try:
            if win:
                x = win.left + int(win.width * 0.42)
                y = win.top + int(win.height * 0.47)
            else:
                width, height = pyautogui.size()
                x = int(width * 0.443)
                y = int(height * 0.315)
            pyautogui.click(x, y)
            time.sleep(0.3)
        except Exception:
            pass

    def _confirm_ukey_window(self, win):
        logger.info("提交 U 盾密码窗口...")
        if win and self._click_ukey_confirm_button_win32(win):
            return
        # AiKeyboard 回车
        if self._send_with_aikeyboard('Enter'):
            time.sleep(2)
            if win and not self._is_ukey_window_alive(win):
                return
        if pyautogui:
            try:
                if win:
                    self._activate_ukey_window(win)
                pyautogui.press("enter")
                time.sleep(0.5)
                pyautogui.press("enter")
                if win and not self._is_ukey_window_alive(win):
                    return
            except Exception:
                pass
            try:
                if win:
                    self._activate_ukey_window(win)
                    x = win.left + int(win.width * 0.27)
                    y = win.top + int(win.height * 0.88)
                    pyautogui.click(x, y)
                    return
            except Exception:
                pass
        try:
            self.page.keyboard.press("Enter")
        except Exception:
            pass

    def _click_ukey_confirm_button_win32(self, win) -> bool:
        hwnd = getattr(win, "_hWnd", None) if win else None
        if not hwnd:
            return False
        try:
            user32 = ctypes.windll.user32
            candidates = []
            EnumChildProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

            def get_text(child_hwnd):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(child_hwnd, buf, 256)
                return buf.value.strip()

            def enum_child(child_hwnd, _):
                text = get_text(child_hwnd)
                if text in ("确认", "确定", "OK", "Ok", "ok"):
                    candidates.append(child_hwnd)
                return True

            user32.EnumChildWindows(hwnd, EnumChildProc(enum_child), 0)
            if not candidates:
                return False
            BM_CLICK = 0x00F5
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.2)
            user32.SendMessageW(candidates[0], BM_CLICK, 0, 0)
            time.sleep(1)
            return not self._is_ukey_window_alive(win)
        except Exception:
            return False

    def _is_ukey_window_alive(self, win) -> bool:
        hwnd = getattr(win, "_hWnd", None) if win else None
        if not hwnd:
            return False
        try:
            user32 = ctypes.windll.user32
            return bool(user32.IsWindow(hwnd) and user32.IsWindowVisible(hwnd))
        except Exception:
            return False

    @staticmethod
    def _input_with_aikeyboard(text: str) -> bool:
        try:
            from common.keyboard_helper import input_with_aikeyboard
            return input_with_aikeyboard(text)
        except Exception:
            return False

    @staticmethod
    def _send_with_aikeyboard(key: str) -> bool:
        try:
            from common.keyboard_helper import send_with_aikeyboard
            return send_with_aikeyboard(key)
        except Exception:
            return False

    # ==================== 通用工具 ====================

    def _dismiss_popups(self):
        """清理页面弹窗和遮挡层"""
        for label, context in self._iter_contexts():
            try:
                result = context.evaluate("""
                    () => {
                        const modalSelectors = [
                            '.modal.in', '.modal.show', '.modal-dialog', '.modal-content',
                            '[role="dialog"]', '.layui-layer', '.el-message-box',
                            '.ant-modal-content', '.sweet-alert',
                            '.pop_body', '.popup', '.ui-dialog', '.xubox_layer'
                        ];
                        let clicked = [];
                        const isVisible = (el) => {
                            const rect = el.getBoundingClientRect();
                            const style = window.getComputedStyle(el);
                            return rect.width > 0 && rect.height > 0
                                && style.display !== 'none'
                                && style.visibility !== 'hidden';
                        };
                        const clickButtonIn = (root) => {
                            const btns = root.querySelectorAll('button, input[type="button"], a, .close, .layui-layer-close, [aria-label="Close"]');
                            for (const btn of btns) {
                                if (!isVisible(btn)) continue;
                                const txt = (btn.textContent || btn.value || '').trim();
                                if (['关闭','确定','确认','取消','我知道了','暂不'].includes(txt)
                                    || btn.classList.contains('close')
                                    || btn.getAttribute('aria-label') === 'Close') {
                                    btn.click();
                                    clicked.push(txt || btn.className || 'close');
                                    return true;
                                }
                            }
                            return false;
                        };
                        for (const sel of modalSelectors) {
                            const modals = document.querySelectorAll(sel);
                            for (const modal of modals) {
                                if (!isVisible(modal)) continue;
                                const rect = modal.getBoundingClientRect();
                                if (rect.width < 50 || rect.height < 50) continue;
                                clickButtonIn(modal);
                            }
                        }
                        // 建行常见的“温馨提示”不是标准 dialog，按文本向上找容器。
                        for (const el of document.querySelectorAll('div, table, td')) {
                            const text = (el.textContent || '').replace(/\\s+/g, '');
                            if (!text.includes('温馨提示')) continue;
                            if (!isVisible(el)) continue;
                            if (clickButtonIn(el)) break;
                        }
                        return clicked;
                    }
                """)
                if result:
                    logger.info(f"已清理页面弹窗 ({label}): {result}")
            except Exception:
                pass
        try:
            self.page.keyboard.press('Escape')
        except Exception:
            pass

    def _save_debug_snapshot(self):
        """保存提交前截图和 HTML，便于核对。"""
        try:
            debug_dir = Path(__file__).resolve().parent / "debug"
            debug_dir.mkdir(exist_ok=True)
            date_name = time.strftime("%Y-%m-%d")
            path = debug_dir / f"{date_name}_ccb_制单提交前.png"
            self.page.screenshot(path=str(path), full_page=True)
            logger.info(f"已保存制单提交前截图: {path}")
            html_path = debug_dir / f"{date_name}_ccb_制单提交前.html"
            html_path.write_text(self.page.content(), encoding="utf-8")
            logger.info(f"已保存制单提交前 HTML: {html_path}")
        except Exception as e:
            logger.debug(f"保存制单调试截图失败: {e}")

    def _save_result_screenshot(self) -> Optional[Path]:
        """保存提交结果弹窗截图。"""
        try:
            result_dir = Path(__file__).resolve().parent / "records"
            result_dir.mkdir(exist_ok=True)
            path = result_dir / f"{time.strftime('%Y-%m-%d')}_ccb_执行结果.png"
            self.page.screenshot(path=str(path), full_page=False)
            logger.info(f"已保存执行结果截图: {path}")
            return path
        except Exception as e:
            logger.warning(f"保存执行结果截图失败: {e}")
            return None
