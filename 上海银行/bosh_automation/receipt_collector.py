# -*- coding: utf-8 -*-
"""电子回单采集 - 上海银行

流程：首页进入账户明细 → 设置区间查询 → 选择全部 → 导出回单 → 确认下载。
"""
from pathlib import Path

from account_detail import AccountDetailPage, click_confirm, date_range
from logger import setup_logger

logger = setup_logger()

EXPORT_RECEIPT_BUTTON = "#entmanagement > div > div.accountDetails > div.table-button > button:nth-child(4)"
EXPORT_RECEIPT_DIALOG = "#entmanagement > div > div.accountDetails > div:nth-child(8)"
EXPORT_RECEIPT_CONFIRM = f"{EXPORT_RECEIPT_DIALOG} > div > div.eib-dialog__footer > div > button:nth-child(2) > span"
EXPORT_RECEIPT_CONFIRM_BUTTON = (
    f"{EXPORT_RECEIPT_DIALOG} > div > div.eib-dialog__footer > div > button:nth-child(2)"
)
# 回单打包由银行侧生成，给足 2 分钟
DOWNLOAD_TIMEOUT_MS = 120_000


class ReceiptCollector:
    def __init__(self, page, config):
        self.page = page
        self.config = config
        self.download_dir = Path(config.download_path)
        self.detail = AccountDetailPage(page, config)

    def collect(self) -> list:
        """采集回单，返回下载文件路径；无数据时返回 ["当前查询结果为空"]。"""
        self.download_dir.mkdir(parents=True, exist_ok=True)

        start, end = date_range(self.config)
        self.detail.open()
        if not self.detail.query(start, end):
            logger.info("查询结果为空")
            return ["当前查询结果为空"]

        self.detail.select_all()
        return [self._export(start, end)]

    def _export(self, start: str, end: str) -> str:
        logger.info("打开导出回单弹窗...")
        self.page.locator(EXPORT_RECEIPT_BUTTON).first.click()
        try:
            self.page.locator(EXPORT_RECEIPT_CONFIRM_BUTTON).first.wait_for(state="visible", timeout=5000)
        except Exception:
            logger.info("导出回单弹窗未在 5 秒内出现，仍继续尝试")

        logger.info("确认导出回单...")
        with self.page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download_info:
            click_confirm(self.page, EXPORT_RECEIPT_CONFIRM, EXPORT_RECEIPT_CONFIRM_BUTTON)
        download = download_info.value

        suffix = Path(download.suggested_filename).suffix or ".zip"
        target = self.download_dir / f"上海银行_电子回单_{start}_{end}{suffix}"
        download.save_as(str(target))
        logger.info(f"回单已保存: {target}")
        return str(target.resolve())
