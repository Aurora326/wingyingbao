# -*- coding: utf-8 -*-
"""制单 - 上海银行批量转账导入

流程：平台付款协同 Excel → 按上海银行模板生成导入文件 → 打开批量转账新增申请页
→ 点「导入文件」上传 → 读导入结果 → 点「下一步」并确认温馨提示 → 停在确认页；
action=submit 时再点「提交」，由机械臂按压 U 盾 OK 键并等待提交成功。

页面元素来自 2026-11-16 实际页面。付款账户切换尚未实现，导入前需确保页面
选中的就是任务付款账户，否则会从页面默认账户制单。
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
NEXT_BUTTON = (
    "#basis > div > div.batch-apply > div > div:nth-child(6) > div.box-item "
    "> button.eibbutton.ml-12.eibbutton--primary.eibbutton--medium > span"
)
TIP_CONFIRM = (
    "body > div.my-dialog > div > div > div.eib-dialog__body "
    "> div.bottom-btn > button:nth-child(2) > span"
)
SUBMIT_BUTTON = (
    "#basis > div > div.batch-apply > div > div.flex-center.mt-24 "
    "> button:nth-child(2) > span"
)


def _amount(value) -> str:
    try:
        return f"{Decimal(str(value).replace(',', '').strip()):.2f}"
    except (InvalidOperation, ValueError):
        return "0.00"


class PaymentCreator:
    """上海银行批量转账导入制单"""

    def __init__(self, page, config, cabinet_controller=None):
        self.page = page
        self.config = config
        self.cabinet_controller = cabinet_controller
        self.items = list(getattr(config, "items", None) or [])
        self.action = getattr(config, "action", "save") or "save"

    def create(self) -> dict:
        try:
            path = self._generate_import_file()
            self._open_page()
            self._upload(path)
            self._wait_import()
            self._go_next()
            shot = self._screenshot("payment_before_submit")
            if self.action == "submit":
                ok, message = self._submit()
                return {"批量转账": ("成功: " if ok else "失败: ") + message, "截图": shot}
            return {"批量转账": f"成功: 已导入 {len(self.items)} 笔，已进入确认页，待人工复核", "截图": shot}
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

    def _go_next(self) -> None:
        """点「下一步」并确认温馨提示，停在确认页（save 模式）。"""
        logger.info("点击「下一步」...")
        self.page.locator(NEXT_BUTTON).first.click()
        try:
            self.page.locator(TIP_CONFIRM).first.wait_for(state="visible", timeout=8000)
        except Exception:
            logger.info("未出现温馨提示弹窗，直接进入下一步")
            return
        logger.info("确认温馨提示...")
        self.page.locator(TIP_CONFIRM).first.click()
        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        time.sleep(1)

    def _submit(self) -> tuple[bool, str]:
        """提交批次：点提交 → 机械臂按压 U 盾 OK 键 → 等「提交成功」。"""
        logger.info("点击「提交」...")
        self.page.locator(SUBMIT_BUTTON).first.click(timeout=10000)

        if self._wait_page_text(("按键提示", "秒内按键"), timeout=20):
            logger.info("页面提示需要按压 U 盾 OK 键")
        else:
            logger.warning("提交后未读到按键提示，仍尝试按压 OK 键")

        if self.cabinet_controller is None:
            logger.warning("没有机柜控制器，无法按压 U 盾 OK 键")
            return False, "需要按压 U 盾 OK 键，当前没有机柜控制器"

        if not self.cabinet_controller.click_ok():
            logger.warning("机柜按压 OK 键失败")
            return False, "机柜按压 U 盾 OK 键失败"

        if self._wait_page_text(("提交成功",), timeout=90):
            logger.info("提交成功，等待审批")
            return True, f"已提交 {len(self.items)} 笔，等待审批"
        return False, "按压 OK 键后未等到「提交成功」提示"

    def _wait_page_text(self, texts: tuple[str, ...], timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                body = self.page.inner_text("body")
            except Exception:
                body = ""
            if any(text in body for text in texts):
                return True
            time.sleep(0.5)
        return False

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
