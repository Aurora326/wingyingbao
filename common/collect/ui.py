from __future__ import annotations

from typing import Any


def find(scope: Any, selectors: list[str], timeout: int = 2000, require_visible: bool = True):
    for selector in selectors or []:
        try:
            locator = scope.locator(selector).first
            # count() 是即时调用；is_visible() 不能传 timeout（Playwright 会忽略并套用
            # 页面默认的 30s，导致一个不存在的选择器白等 30 秒）
            if locator.count() == 0:
                continue
            if require_visible and not locator.is_visible():
                continue
            return locator
        except Exception:
            continue
    return None


def exists(scope: Any, selectors: list[str], timeout: int = 0) -> bool:
    for selector in selectors or []:
        try:
            if scope.locator(selector).count() > 0:
                return True
        except Exception:
            continue
        if timeout:
            try:
                scope.wait_for_timeout(timeout)
            except Exception:
                pass
    return False


def click(scope: Any, selectors: list[str], timeout: int = 2000, force: bool = False) -> bool:
    locator = find(scope, selectors, timeout=timeout)
    if locator is None:
        return False
    try:
        locator.click(timeout=timeout, force=force)
        return True
    except Exception:
        try:
            locator.evaluate("el => el.click()")
            return True
        except Exception:
            return False


def click_js(scope: Any, selectors: list[str], timeout: int = 2000) -> bool:
    locator = find(scope, selectors, timeout=timeout)
    if locator is None:
        return False
    try:
        locator.evaluate("el => el.click()")
        return True
    except Exception:
        return False


def fill(scope: Any, selectors: list[str], value: str, timeout: int = 2000) -> bool:
    locator = find(scope, selectors, timeout=timeout)
    if locator is None:
        return False
    try:
        locator.fill("")
        locator.fill(value)
        return True
    except Exception:
        return False


def any_visible(scope: Any, selectors: list[str], timeout: int = 1000) -> bool:
    return find(scope, selectors, timeout=timeout) is not None


def any_text_visible(scope: Any, texts: list[str], timeout: int = 1000) -> bool:
    for text in texts or []:
        try:
            if scope.locator(f"text={text}").first.is_visible(timeout=timeout):
                return True
        except Exception:
            continue
    return False


def count_rows(scope: Any, selectors: list[str]) -> int:
    for selector in selectors or []:
        try:
            total = scope.locator(selector).count()
            if total:
                return total
        except Exception:
            continue
    return 0


def click_text(scope: Any, texts: list[str], timeout: int = 2000) -> bool:
    return click(scope, [f"text={t}" for t in texts or []], timeout=timeout)


def scroll_into_view(scope: Any, selectors: list[str]) -> bool:
    locator = find(scope, selectors, timeout=1000, require_visible=False)
    if locator is None:
        return False
    try:
        locator.scroll_into_view_if_needed(timeout=2000)
        return True
    except Exception:
        return False


def value_of(scope: Any, selectors: list[str]) -> str:
    locator = find(scope, selectors, timeout=500, require_visible=False)
    if locator is None:
        return ""
    try:
        return locator.input_value(timeout=1000) or ""
    except Exception:
        return ""
