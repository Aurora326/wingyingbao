# -*- coding: utf-8 -*-
"""制单 - 上海银行批量转账导入

流程：平台付款协同 Excel → 按上海银行模板生成导入文件 → 打开批量转账新增申请页
→ 校验付款方账号 → 点「导入文件」上传 → 等导入结果 → 停在提交前（save 模式）。

页面元素来自 2026-11-16 实际页面；付款账户选择器尚未取得，
当前只做校验：页面选中的账号必须与任务账户一致，否则直接报错，避免从错误账户付款。
"""
import re
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

import xlrd
from xlutils.copy import copy as xlutils_copy

from account_detail import click_visible_text
from logger import setup_logger

logger = setup_logger()

BATCH_URL = "https://ibank.bosc.cn:6074/eib/?#/basis/etransfer/transfer/batch/apply/pre"
IMPORT_BUTTON = (
    "#basis > div > div.batch-apply > div > div.flex-center-between "
    "> div:nth-child(1) > div > div > button > span"
)


def _amount(value) -> str:
    try:
        return f"{Decimal(str(value).replace(',', '').strip()):.2f}"
    except (InvalidOperation, ValueError):
        return "0.00"


class PaymentCreator:
    """上海银行批量转账导入制单"""

    def __init__(self, page, config):
        self.page = page
        self.config = config
        self.items = list(getattr(config, "items", None) or [])
        self.action = getattr(config, "action", "save") or "save"

    def create(self) -> dict:
        try:
            path = self._generate_import_file()
            self._open_page()
            self._ensure_payer()
            self._upload(path)
            self._wait_import()
            shot = self._screenshot("payment_import_done")
            if self.action == "submit":
                logger.warning("submit 模式尚未实现，本次停在导入完成后，请人工复核提交")
            return {"批量转账": f"成功: 已导入 {len(self.items)} 笔，待人工复核", "截图": shot}
        except Exception as e:  # noqa: BLE001
            logger.error(f"制单失败: {e}")
            shot = self._screenshot("payment_failed")
            return {"批量转账": f"失败: {e}", "截图": shot}

    # ==================== 导入文件 ====================

    def _resolve(self, path: str) -> Path:
        target = Path(path)
        if target.is_absolute():
            return target
        return Path(__file__).resolve().parent / target

    def _generate_import_file(self) -> Path:
        """按模板生成导入文件：第 1 行表头，第 2 行起写数据。"""
        if not self.items:
            raise RuntimeError("没有可制单的付款明细")

        template = self._resolve(self.config.template_file or "template/上海银行批量转账导入文件模板.xls")
        output = self._resolve(self.config.generated_file or "temp/payment_import.xls")
        if not template.exists():
            raise RuntimeError(f"导入模板不存在: {template}")
        output.parent.mkdir(parents=True, exist_ok=True)

        book = xlrd.open_workbook(str(template), formatting_info=True)
        sheet = book.sheet_by_index(0)
        writable = xlutils_copy(book)
        target = writable.get_sheet(0)

        for offset, item in enumerate(self.items):
            row = sheet.nrows + offset
            purpose = str(item.get("purpose") or "")[:35]
            target.write(row, 0, str(item.get("payee_account") or ""))
            target.write(row, 1, str(item.get("payee_name") or ""))
            target.write(row, 2, str(item.get("payee_branch") or item.get("payee_bank") or ""))
            target.write(row, 3, str(item.get("payee_bank_code") or ""))
            target.write(row, 4, _amount(item.get("amount")))
            target.write(row, 5, purpose)
            target.write(row, 6, str(item.get("remark") or ""))
            target.write(row, 7, "")

        book.release_resources()
        writable.save(str(output))
        logger.info(f"已生成导入文件: {output}（{len(self.items)} 笔）")
        return output

    # ==================== 页面操作 ====================

    def _open_page(self) -> None:
        logger.info("打开批量转账新增申请页...")
        self.page.goto(BATCH_URL)
        self.page.locator(IMPORT_BUTTON).first.wait_for(state="visible", timeout=30000)

    def _ensure_payer(self) -> None:
        """页面选中的付款账号必须与任务账户一致，否则停止，避免从错误账户付款。"""
        expected = str(getattr(self.config, "payer_account", "") or "").strip()
        actual = ""
        deadline = time.time() + 10
        while time.time() < deadline and not actual:
            actual = self._current_payer_text()
            if not actual:
                time.sleep(1)
        if not actual:
            logger.warning("未读到页面付款方账号，跳过账户校验")
            return
        logger.info(f"付款方账号: {actual}")
        if expected and expected not in actual:
            raise RuntimeError(f"页面付款账户不是任务账户 {expected}，请先配置账户切换（当前: {actual}）")

    def _current_payer_text(self) -> str:
        try:
            return self.page.evaluate(
                """() => {
                    const nodes = [...document.querySelectorAll('#basis .batch-apply *')]
                        .filter(el => el.children.length === 0
                            && /^\\d{6,}\\//.test((el.textContent || '').trim()));
                    return nodes.length ? nodes[nodes.length - 1].textContent.trim() : '';
                }"""
            )
        except Exception:
            return ""

    def _upload(self, path: Path) -> None:
        logger.info("上传导入文件...")
        try:
            with self.page.expect_file_chooser(timeout=10000) as chooser:
                self.page.locator(IMPORT_BUTTON).first.click()
            chooser.value.set_files(str(path))
        except Exception:
            # 有的页面不弹系统文件选择框，直接挂到隐藏的 file input 上
            self.page.locator("input[type=file]").first.set_input_files(str(path))
        logger.info("文件已提交，等待导入结果...")

    def _wait_import(self, timeout: float = 90) -> None:
        """等导入结果；有行错误时先点「自动匹配开户行信息」再复查一次。"""
        deadline = time.time() + timeout
        summary = None
        while time.time() < deadline:
            summary = self._import_summary()
            if summary is not None:
                break
            time.sleep(1)
        if summary is None:
            raise RuntimeError("等待导入结果超时（90 秒）")

        total, errors = summary
        logger.info(f"导入结果: 共 {total} 条，错误 {errors} 条")
        if errors:
            if click_visible_text(self.page, "自动匹配开户行信息", timeout=5):
                logger.info("已点击「自动匹配开户行信息」，等待银行回填...")
                time.sleep(5)
                summary = self._import_summary()
                if summary:
                    total, errors = summary
                    logger.info(f"匹配后结果: 共 {total} 条，错误 {errors} 条")
        if errors:
            raise RuntimeError(f"导入完成但有 {errors} 条错误，请查看截图和页面错误信息")
        logger.info("导入成功")

    def _import_summary(self):
        """读「共导入 N 条数据，错误 M 条」，没出现时返回 None。"""
        try:
            text = self.page.inner_text("body")
        except Exception:
            return None
        match = re.search(r"共导入\s*(\d+)\s*条数据[，,]\s*错误\s*(\d+)\s*条", text)
        if not match:
            return None
        return int(match.group(1)), int(match.group(2))

    def _screenshot(self, name: str) -> str:
        try:
            import datetime as dt

            path = Path("debug") / f"{name}_{dt.datetime.now():%Y%m%d_%H%M%S}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(path))
            return str(path.resolve())
        except Exception:
            return ""
