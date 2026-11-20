# -*- coding: utf-8 -*-
"""账户明细页公共操作：进入页面、设置日期、查询、选择全部。

流水和回单都在同一个页面上完成，选择器来自实际页面（2026-11-13 抓取）。
"""
import datetime as dt
import time

from common.keyboard_helper import input_with_aikeyboard, send_with_aikeyboard
from logger import setup_logger

logger = setup_logger()

# 首页账户卡片上的「交易明细」入口
DETAIL_ENTRY_SELECTOR = (
    "body > div.layout > main > div.layout-container-right > div > div > div > div.main-right "
    "> div.user.component.custom > div.user-bottom > div:nth-child(2) > span"
)
ROOT = "#entmanagement > div > div.accountDetails"
START_DATE_SELECTOR = (
    f"{ROOT} > form > div:nth-child(2) > div.table-form-layout.eib-col.eib-col-8 "
    "> div > div > div:nth-child(1) > div > div > input"
)
END_DATE_SELECTOR = (
    f"{ROOT} > form > div:nth-child(2) > div.table-form-layout.eib-col.eib-col-8 "
    "> div > div > div:nth-child(3) > div > div > input"
)
QUERY_BUTTON_SELECTOR = f"{ROOT} > form > div.eib-row.eib-row--flex > div.eib-col.eib-col-6 > button:nth-child(5)"
SELECT_ALL_SELECTOR = (
    f"{ROOT} > div.pagination.eib-row.is-justify-space-between.eib-row--flex "
    "> div.eib-col.eib-col-11 > button > span"
)
# 查询无数据时表格出现的空状态容器（F12 取自实际页面）
EMPTY_SELECTOR = f"{ROOT} > div.eib-table > div.eib-table__body-wrapper.is-empty"


def click_visible_text(page, text: str, timeout: float = 10.0) -> bool:
    """点击页面上可见的指定文字元素（菜单项、弹窗按钮）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if page.evaluate(
                """(text) => {
                    const nodes = [...document.querySelectorAll('li,a,span,div,button')]
                        .filter(el => el.getClientRects().length > 0 && (el.textContent || '').trim() === text);
                    if (!nodes.length) return false;
                    nodes[nodes.length - 1].click();
                    return true;
                }""",
                text,
            ):
                return True
        except Exception:
            pass

        items = page.locator(f"text={text}")
        for index in range(items.count() - 1, -1, -1):
            item = items.nth(index)
            try:
                if item.is_visible():
                    item.click()
                    return True
            except Exception:
                continue
        time.sleep(0.5)
    return False


def date_range(config) -> tuple[str, str]:
    """采集区间：优先用任务下发的日期，否则按 days_back 往前推。"""
    today = dt.date.today()
    end = config.end_date or today.isoformat()
    start = config.start_date or (today - dt.timedelta(days=config.days_back)).isoformat()
    return start, end


def click_confirm(page, *selectors: str) -> None:
    """点弹窗「确认」：按顺序试选择器，失败退到脚本点击和文字点击。"""
    for selector in selectors:
        try:
            page.locator(selector).first.click(timeout=5000)
            return
        except Exception as e:
            logger.debug(f"点击 {selector} 失败: {e}")

    for selector in selectors:
        try:
            clicked = page.evaluate(
                """(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return false;
                    el.click();
                    return true;
                }""",
                selector,
            )
            if clicked:
                return
        except Exception as e:
            logger.debug(f"脚本点击 {selector} 失败: {e}")

    if not click_visible_text(page, "确认"):
        raise RuntimeError("弹窗里没找到确认按钮")


class AccountDetailPage:
    def __init__(self, page, config):
        self.page = page
        self.config = config

    def open(self) -> None:
        """从首页卡片进入账户明细；已经在明细页时直接复用。"""
        if self._root_visible():
            logger.info("账户明细页已打开")
            return
        logger.info("进入账户明细查询...")
        entry = self.page.locator(DETAIL_ENTRY_SELECTOR).first
        entry.wait_for(state="visible", timeout=15000)
        entry.click()
        self._root().wait_for(state="visible", timeout=30000)
        time.sleep(2)

    def query(self, start: str, end: str) -> bool:
        """设置区间并查询；返回是否有数据。"""
        self._set_date(START_DATE_SELECTOR, start, "开始日期")
        self._set_date(END_DATE_SELECTOR, end, "结束日期")
        logger.info(f"查询区间: {start} ~ {end}")

        self.page.locator(QUERY_BUTTON_SELECTOR).first.click()
        time.sleep(3)
        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        return not self._has_no_data()

    def select_all(self) -> bool:
        """选择全部明细；没有可全选的数据（查询为空）时返回 False。"""
        logger.info("选择全部明细...")
        locator = self.page.locator(SELECT_ALL_SELECTOR).first
        try:
            locator.wait_for(state="visible", timeout=5000)
        except Exception:
            logger.info("页面上没有「选择全部」，按查询结果为空处理")
            return False
        locator.click()
        time.sleep(1)
        return True

    def _root(self):
        return self.page.locator(ROOT).first

    def _root_visible(self) -> bool:
        try:
            return self._root().is_visible(timeout=2000)
        except Exception:
            return False

    def _set_date(self, selector: str, value: str, label: str) -> None:
        """日期输入框：能直接填就填，只读时点击聚焦后用 AiKeyboard 输入。"""
        field = self.page.locator(selector).first
        field.wait_for(state="visible", timeout=10000)
        field.click()
        time.sleep(0.4)
        try:
            field.fill(value)
        except Exception:
            pass

        try:
            current = field.input_value()
        except Exception:
            current = ""
        if current == value:
            return

        logger.info(f"{label} 直接填写未生效，改用 AiKeyboard 输入...")
        field.click()
        for _ in range(len(current) + 6):
            send_with_aikeyboard("backspace")
        if not input_with_aikeyboard(value):
            raise RuntimeError(f"{label} 输入失败：AiKeyboard 不可用")
        time.sleep(0.3)

    def _has_no_data(self) -> bool:
        try:
            if self.page.locator(EMPTY_SELECTOR).first.is_visible(timeout=1000):
                return True
        except Exception:
            pass
        try:
            body = self.page.inner_text("body")
        except Exception:
            return False
        return any(marker in body for marker in ("共 0 笔", "共0笔", "暂无数据"))
