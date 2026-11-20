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
from pathlib import Path
from typing import Any, Dict, List, Optional
from playwright.sync_api import Page
from loguru import logger

import batch_template
from common.frame_helper import (
    click_button_texts,
    click_text_in_context,
    iter_contexts,
)
from popup_handler import PopupHandlerMixin
from pin_inputter import handle_pin_dialog



class PaymentCreator(PopupHandlerMixin):
    """建设银行批量模板导入制单"""

    DEFAULT_TEMPLATE_MAPPING = batch_template.DEFAULT_TEMPLATE_MAPPING

    def __init__(self, page: Page, config: Dict, pin: str = "", cabinet_controller=None, diagnostics_dir=None):
        self.page = page
        self.config = config
        self.action = config.get('action', 'save')
        self.pin = pin
        self.cabinet_controller = cabinet_controller
        self.diagnostics_dir = Path(diagnostics_dir) if diagnostics_dir else Path(__file__).resolve().parent / "debug"
        self._snapshot_files: List[str] = []
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

    # ==================== 模板映射与文件生成 ====================

    def _mapping_path(self) -> Path:
        mapping_file = self.config.get("template_mapping_file") or "template/payments_batch_template.json"
        return self._resolve_path(mapping_file)

    def _load_template_mapping(self) -> Dict[str, Any]:
        return batch_template.load_mapping(self._mapping_path())

    def _mapped_value(self, item: Dict, column_cfg: Dict[str, Any], row_index: int) -> str:
        return batch_template.mapped_value(item, column_cfg, row_index, self.config)

    def _generate_batch_import_file(self) -> Path:
        """按建行批量转账模板生成导入文件。"""
        template_file = self.config.get("template_file") or "template/批量转账模板.xls"
        generated_file = self.config.get("generated_file") or "temp/payment_import.xls"
        return batch_template.generate_import_file(
            self._resolve_path(template_file),
            self._resolve_path(generated_file),
            self.items,
            self._mapping_path(),
            self.config,
        )

    # ==================== 页面导航 ====================

    def _navigate_to_batch_payment_page(self):
        """通过菜单导航到批量转账文件录入页面。"""
        logger.info("导航到批量转账制单页面...")
        try:
            self._dismiss_in_page_popups(self.page)
            # 策略1：尝试直接通过菜单文本定位并点击
            self._click_menu_by_text("转账业务")
            time.sleep(1)
            clicked_file_entry = False
            for text in ("转账制单",):
                clicked = self._click_menu_by_text(text)
                if not clicked:
                    logger.warning(f"未直接点击到菜单: {text}")
                time.sleep(0.8)
                self._dismiss_in_page_popups(self.page)
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
        """遍历主页面和 iframe（公共实现见 common/frame_helper）。"""
        return iter_contexts(self.page)

    @staticmethod
    def _click_text_in_context(context, text: str, exact: bool = True) -> Dict[str, Any]:
        """在一个页面上下文里点击最小的可见文本元素。"""
        return click_text_in_context(context, text, action="click", exact=exact)

    @staticmethod
    def _interact_text_in_context(context, text: str, action: str = "click", exact: bool = True) -> Dict[str, Any]:
        """在一个页面上下文里对最小的可见文本元素触发 hover/click。"""
        return click_text_in_context(context, text, action=action, exact=exact)

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
        return click_button_texts(
            self.page,
            texts,
            preferred_context=preferred_context,
            preferred_label=preferred_label,
        )

    def _finish_batch_action(self) -> str:
        """完成批量操作。"""
        if self.action == 'submit':
            self._dismiss_marketing_overlays()
            self._dismiss_in_page_popups(self.page)
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
            self._dismiss_in_page_popups(self.page)
            self._upload_batch_file(import_file)
            self._fill_batch_form()
            # 手机银行广告弹窗会延迟出现，逐 frame 关闭并连续检查几次
            time.sleep(1.5)
            for _ in range(4):
                self._dismiss_marketing_overlays()
                self._dismiss_in_page_popups(self.page)
                time.sleep(1)
            self._save_debug_snapshot()
            result = self._finish_batch_action()
            results["batch_import"] = result
        except Exception as e:
            logger.error(f"批量模板导入制单失败: {e}")
            results["batch_import"] = f"失败: {e}"
            self._save_debug_snapshot()

        shot = next(
            (p for p in self._snapshot_files if str(p).lower().endswith((".jpg", ".png"))), ""
        )
        if shot:
            results["截图"] = shot
        if self._snapshot_files:
            results["diagnostic_files"] = list(self._snapshot_files)
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
        """处理交易签名时的 U 盾密码弹窗（复用 pin_inputter）。"""
        if not self.pin:
            logger.info("未配置 PIN，跳过 U 盾密码弹窗自动输入")
            return
        logger.info("等待 U 盾密码弹窗出现...")
        handle_pin_dialog(self.pin)
        time.sleep(5)

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

    @staticmethod
    def _send_with_aikeyboard(key: str) -> bool:
        try:
            from common.keyboard_helper import send_with_aikeyboard
            return send_with_aikeyboard(key)
        except Exception:
            return False

    # ==================== 通用工具 ====================



    def _capture_snapshot(self, stage: str) -> List[str]:
        """用统一诊断模块保存截图 + 脱敏 DOM，返回文件列表。"""
        try:
            from common.diagnostics import capture_snapshot

            files = capture_snapshot(self.page, self.diagnostics_dir, stage=stage)
            self._snapshot_files.extend(files)
            if files:
                logger.info(f"已保存截图与页面快照({stage}): {files}")
            return files
        except Exception as e:
            logger.warning(f"保存页面快照失败: {e}")
            return []

    def _save_debug_snapshot(self):
        """保存提交前现场，便于核对。"""
        self._capture_snapshot("制单提交前")

    def _save_result_screenshot(self) -> Optional[Path]:
        """保存提交结果弹窗截图，返回图片路径。"""
        files = self._capture_snapshot("制单执行结果")
        for item in files:
            if str(item).lower().endswith((".jpg", ".png")):
                return Path(item)
        return None
