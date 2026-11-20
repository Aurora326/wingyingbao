# -*- coding: utf-8 -*-
"""建设银行账户明细页公共操作：导航、账户全选、日期查询、结果页定位。

流水与回单采集共用：两者从首页到“交易明细查询结果页”的步骤完全一致，
只有最后的下载动作不同。
"""
import time

from common.frame_helper import find_frame_containing, set_input_value
from logger import setup_logger
from popup_handler import PopupHandlerMixin

logger = setup_logger()

RESULT_SELECTORS = ['#dlAll', 'input[name="all"]']
QUERY_FRAME_SELECTORS = ['#StDt', '#EdDt', 'input[name="Submit4"]']
CONFIRM_SELECTORS = [
    'input[type="submit"][name="Submit4"][value="确定"]',
    'input[name="Submit4"]',
    'input[type="submit"][name="Submit1"]',
    'input[name="Submit1"]',
    'input[type="submit"][value="确定"]',
    'input[value="确定"]',
]


def _to_ccb_date(date_str: str) -> str:
    """将 YYYY-MM-DD 转换为 CCB 需要的 YYYYMMDD。"""
    return date_str.replace('-', '')


class CCBAccountDetailPage(PopupHandlerMixin):
    """建行账户明细页；实例保存 work_frame / result_frame 供下载阶段使用。"""

    def __init__(self, page):
        self.page = page
        self.work_frame = None
        self.result_frame = None

    # ==================== 对外入口 ====================

    def is_result_page(self) -> bool:
        frame = find_frame_containing(self.page, RESULT_SELECTORS, timeout=3, prefer_visible=False)
        if frame is not None:
            self.result_frame = frame
            return True
        return False

    def is_empty_result(self) -> bool:
        """查询是否无交易：结果页分页总数 #HZDIV > div > span.curPageNum 为 0。"""
        contexts = []
        for context in (self.result_frame, self.work_frame):
            if context is not None and context not in contexts:
                contexts.append(context)
        for frame in self.page.frames:
            if frame not in contexts:
                contexts.append(frame)
        contexts.append(self.page)

        for context in contexts:
            try:
                locator = context.locator('#HZDIV > div > span.curPageNum').first
                if locator.count() == 0:
                    continue
                text = (locator.text_content() or '').strip()
                logger.info(f"结果页总页数(#HZDIV .curPageNum): {text!r}")
                return text == '0'
            except Exception as e:
                logger.debug(f"读取分页总数失败: {e}")
        logger.warning("未找到分页元素 #HZDIV > div > span.curPageNum")
        return False

    def open_and_query(self, start_date: str, end_date: str):
        """从任意页面导航到交易明细结果页并完成查询；返回结果 frame，失败返回 None。"""
        if self.is_result_page():
            logger.info("已在交易明细结果页")
            return self.result_frame

        self._open_query_page()
        self._set_dates(start_date, end_date)
        self._click_confirm()

        result_frame = find_frame_containing(
            self.page, RESULT_SELECTORS, timeout=15, prefer_visible=False
        )
        if result_frame is None:
            self.result_frame = None
            return None
        self.result_frame = result_frame
        self.work_frame = result_frame
        logger.info("已进入交易明细结果页")
        return result_frame

    # ==================== 页面导航 ====================

    def _open_query_page(self) -> None:
        logger.info("导航到交易明细查询页面...")
        time.sleep(2)

        try:
            self.page.keyboard.press("Escape")
            time.sleep(0.5)
        except Exception:
            pass

        if not self._dismiss_marketing_overlays(timeout=15):
            logger.info("未发现首页广告弹窗")

        # Step 1: 首页点击“账户查询”（在 iframe 中）
        frame = find_frame_containing(self.page, ['#toAccQuery'], timeout=10, prefer_visible=False)
        if not frame:
            raise Exception("未找到账户查询链接(#toAccQuery)")

        logger.info("点击账户查询...")
        link = frame.locator('#toAccQuery').first
        try:
            link.click(timeout=5000)
        except Exception as e:
            logger.warning(f"常规点击账户查询失败（{e}），改用 JS 点击")
            frame.evaluate("() => { const el = document.querySelector('#toAccQuery'); if (el) el.click(); }")
        time.sleep(3)

        # Step 2: 账户列表页勾选“全选”
        frame = find_frame_containing(self.page, ['#all_0'], timeout=10, prefer_visible=False)
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

        # Step 3: 点击“交易明细”
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

        # Step 4: 等待进入查询条件页
        frame = find_frame_containing(
            self.page, QUERY_FRAME_SELECTORS, timeout=10, match_all=True, prefer_visible=True
        )
        if not frame:
            raise Exception("未进入交易明细查询页")
        self.work_frame = frame
        logger.info("成功进入交易明细查询页面")

    # ==================== 日期与查询 ====================

    def _set_dates(self, start_date: str, end_date: str) -> None:
        logger.info(f"设置日期范围: {start_date} 至 {end_date}")
        frame = self.work_frame
        if not frame:
            frame = find_frame_containing(
                self.page, QUERY_FRAME_SELECTORS, timeout=5, match_all=True, prefer_visible=True
            )
            self.work_frame = frame
        if not frame:
            raise Exception("未找到交易明细查询页")

        start_ccb = _to_ccb_date(start_date)
        end_ccb = _to_ccb_date(end_date)
        set_input_value(frame, '#StDt', start_ccb)
        set_input_value(frame, '#EdDt', end_ccb)

        # 关闭可能弹出的日历
        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass
        time.sleep(0.3)
        logger.info(f"日期已设置: {start_ccb} 至 {end_ccb}")

    def _click_confirm(self) -> str:
        """点击交易明细查询页“确定”按钮。"""
        logger.info("点击确定查询...")
        self._log_frame_diagnostics("确定按钮前")

        contexts = []
        if self.work_frame is not None:
            contexts.append(("work_frame", self.work_frame))
        for idx, frame in enumerate(self.page.frames):
            if frame == self.work_frame:
                continue
            contexts.append((f"frame[{idx}]", frame))
        contexts.append(("page", self.page))

        for label, context in contexts:
            for selector in CONFIRM_SELECTORS:
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

    def _log_frame_diagnostics(self, tag: str = "frame诊断") -> None:
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
