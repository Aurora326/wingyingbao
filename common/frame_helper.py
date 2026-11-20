# -*- coding: utf-8 -*-
"""Playwright 页面/iframe 通用操作。

供各银行采集与制单脚本共享：frame 遍历与定位、输入框赋值、按可见文字点击。
只依赖鸭子类型（page/frame 的 locator/evaluate），不 import Playwright，可离线测试。
"""
from __future__ import annotations

import time
from typing import Any, Iterator

from loguru import logger


def iter_contexts(page: Any, include_page: bool = True) -> Iterator[tuple[str, Any]]:
    """依次产出主页面与各 iframe：("page", page), ("frame[0]", frame), ..."""
    if include_page:
        yield "page", page
    for index, frame in enumerate(page.frames):
        yield f"frame[{index}]", frame


def find_frame_containing(
    page: Any,
    selectors: list[str],
    timeout: float = 10.0,
    match_all: bool = False,
    prefer_visible: bool = False,
    poll: float = 0.5,
) -> Any | None:
    """在 iframe 中查找包含目标选择器的 frame。

    prefer_visible=True：优先返回元素可见的 frame，找不到再回退首个命中（流水语义）；
    prefer_visible=False：首个命中立即返回（回单语义）。
    match_all=True：要求所有选择器同时存在（查询条件页/日期页）。
    """
    start = time.time()
    while time.time() - start < timeout:
        fallback = None
        for frame in page.frames:
            try:
                if match_all:
                    if all(frame.locator(selector).count() > 0 for selector in selectors):
                        return frame
                    continue
                for selector in selectors:
                    locator = frame.locator(selector).first
                    if locator.count() == 0:
                        continue
                    if not prefer_visible:
                        return frame
                    if fallback is None:
                        fallback = frame
                    try:
                        if locator.is_visible(timeout=200):
                            return frame
                    except Exception:
                        pass
            except Exception:
                continue
        if fallback is not None:
            return fallback
        time.sleep(poll)
    return None


def set_input_value(
    frame: Any,
    selector: str,
    value: str,
    wait_visible: bool = True,
    settle: float = 0.3,
) -> None:
    """在 iframe 中给输入框赋值并派发 focus/input/change/blur（建行日期控件用）。"""
    input_field = frame.locator(selector).first
    if wait_visible:
        input_field.wait_for(state="visible", timeout=5000)

    frame.evaluate(
        """
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
        """,
        {"selector": selector, "value": value},
    )
    time.sleep(settle)


def click_text_in_context(context: Any, text: str, action: str = "click", exact: bool = True) -> dict:
    """在一个页面上下文里对最小的可见文本元素触发 hover/click。"""
    return context.evaluate(
        """
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
        """,
        {"text": text, "exact": exact, "action": action},
    )


def click_button_texts(
    page: Any,
    texts: list[str],
    preferred_context: Any | None = None,
    preferred_label: str = "",
    delay: float = 3.0,
) -> bool:
    """按按钮文字点击（先 locator，再 JS 兜底），跨主页面与 iframe。"""
    contexts = [(preferred_label or "preferred", preferred_context)] if preferred_context else []
    contexts += list(iter_contexts(page))
    seen = set()
    for ctx_label, context in contexts:
        if not context or id(context) in seen:
            continue
        seen.add(id(context))
        for text in texts:
            try:
                btn = context.locator(
                    f'button:has-text("{text}"), input[value="{text}"], a:has-text("{text}")'
                ).first
                if btn.count() > 0 and btn.is_visible(timeout=1500):
                    btn.click(timeout=5000)
                    logger.info(f"已点击按钮 {text} ({ctx_label})")
                    time.sleep(delay)
                    return True
            except Exception:
                pass
            try:
                result = context.evaluate(
                    """
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
                    """,
                    text,
                )
                if result and result.get("clicked"):
                    logger.info(f"已通过 JS 点击按钮 {text} ({ctx_label})")
                    time.sleep(delay)
                    return True
            except Exception:
                pass
    return False
