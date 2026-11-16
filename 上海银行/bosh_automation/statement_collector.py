# -*- coding: utf-8 -*-
"""银行流水采集 - 上海银行

流程：首页进入账户明细 → 设置区间查询 → 选择全部 → 导出明细（Excel）→ 确认下载。
"""
import time
from pathlib import Path

from account_detail import AccountDetailPage, click_confirm, date_range
from logger import setup_logger

logger = setup_logger()

EXPORT_MENU_TOGGLE = "#exportBtn > div > div.eibexportfile-button-left"
# 点「导出明细」会直接弹出这个弹窗（没有单独的「导出Excel」菜单项）
EXPORT_DIALOG = "#entmanagement > div > div.accountDetails > div:nth-child(9)"
EXPORT_DIALOG_CONFIRM = f"{EXPORT_DIALOG} > div > div.eib-dialog__footer > div > button:nth-child(2) > span"
EXPORT_DIALOG_CONFIRM_BUTTON = (
    f"{EXPORT_DIALOG} > div > div.eib-dialog__footer > div > button:nth-child(2)"
)
# 导出文件由银行侧生成，给足 2 分钟
DOWNLOAD_TIMEOUT_MS = 120_000


class StatementCollector:
    def __init__(self, page, config):
        self.page = page
        self.config = config
        self.download_dir = Path(config.download_path)
        self.detail = AccountDetailPage(page, config)

    def collect(self) -> list:
        """采集流水，返回下载文件路径；无数据时返回 ["当前查询结果为空"]。"""
        self.download_dir.mkdir(parents=True, exist_ok=True)

        start, end = date_range(self.config)
        self.detail.open()
        if not self.detail.query(start, end):
            logger.info("查询结果为空")
            return ["当前查询结果为空"]

        self.detail.select_all()
        return [self._export(start, end)]

    def _export(self, start: str, end: str) -> str:
        logger.info("打开导出明细弹窗...")
        self._open_export_menu()

        logger.info("确认导出...")
        with self.page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download_info:
            click_confirm(self.page, EXPORT_DIALOG_CONFIRM, EXPORT_DIALOG_CONFIRM_BUTTON)
        download = download_info.value

        suffix = Path(download.suggested_filename).suffix or ".xlsx"
        target = self.download_dir / f"上海银行_银行流水_{start}_{end}{suffix}"
        download.save_as(str(target))
        logger.info(f"流水已保存: {target}")
        return str(target.resolve())

    def _open_export_menu(self) -> None:
        """点「导出明细」，等导出弹窗出现。

        这个按钮点下去直接弹「导出明细」对话框，没有下拉菜单可选项。
        """
        if self._wait_dialog():
            logger.info("导出明细弹窗已打开")
            return

        for selector in (f"{EXPORT_MENU_TOGGLE}:visible", "#exportBtn:visible"):
            try:
                self.page.locator(selector).first.click(timeout=5000)
            except Exception as e:
                logger.debug(f"点击导出入口 {selector} 失败: {e}")
                continue
            if self._wait_dialog(5):
                return

        hints = self.page.evaluate(
            """() => [...document.querySelectorAll('li,a,span,div,button')]
                .filter(el => el.getClientRects().length > 0 && (el.textContent || '').includes('导出'))
                .map(el => `${el.tagName}.${el.className || ''}: ${(el.textContent || '').trim().slice(0, 20)}`)
                .slice(0, 15)"""
        )
        logger.info(f"页面上的导出元素: {hints}")
        raise RuntimeError("没打开「导出明细」弹窗")

    def _wait_dialog(self, timeout: float = 0.0) -> bool:
        """以「确认」按钮可见为准判断弹窗，避免外层常驻容器造成误判。"""
        deadline = time.time() + timeout
        while True:
            try:
                if self.page.locator(EXPORT_DIALOG_CONFIRM_BUTTON).first.is_visible(timeout=1000):
                    return True
            except Exception:
                pass
            if time.time() >= deadline:
                return False
            time.sleep(0.3)
