from __future__ import annotations

import datetime as dt
from pathlib import Path

from bankcore.browser import BrowserSession
from bankcore.result import CollectResult
from bankcore.drivers.base import BankDriver, RunContext

HEADERS = [
    "交易日期",
    "交易时间",
    "对方账号",
    "对方账户名称",
    "对方账号开户网点名称",
    "借方发生额",
    "贷方发生额",
    "账户余额",
    "摘要",
    "退汇标识",
    "退汇日期",
    "柜员交易号",
]

SAMPLE_ROWS = [
    ["2026-01-05", "09:12:33", "6222020200112233", "上海隐石信息科技发展有限公司", "招商银行", "", "1200.00", "1200.00", "二代支付", "否", "", "SC0001"],
    ["2026-01-06", "14:03:10", "6222020200112233", "杭州云启网络科技有限公司", "工商银行", "300.00", "", "900.00", "服务费", "否", "", "SC0002"],
    ["2026-01-08", "10:44:02", "6222020200112244", "深圳前海数据服务有限公司", "建设银行", "", "5000.00", "5900.00", "货款", "否", "", "SC0003"],
]


class MockDriver(BankDriver):
    bank_code = "mock"
    display_name = "模拟银行"
    needs_browser = False

    def login(self, ctx: RunContext, session: BrowserSession | None) -> bool:
        ctx.log("mock driver: 跳过登录")
        return True

    def _write_statement(self, ctx: RunContext, start: str, end: str) -> Path:
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "SHEET1"
        sheet.append(["标题", "账户明细查询"])
        sheet.append(["查询人员", "测试"])
        sheet.append(["查询时间", dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
        sheet.append([None, "账号", ctx.task.get("account_no", "MOCK0001")])
        sheet.append([None, "起始日期", start])
        sheet.append([None, "截止日期", end])
        sheet.append([])
        sheet.append(HEADERS)
        for row in SAMPLE_ROWS:
            sheet.append(row)
        target_dir = ctx.downloads_dir / "mock"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"mock_statement_{dt.datetime.now().strftime('%H%M%S')}.xlsx"
        workbook.save(target)
        return target

    def collect_statements(self, ctx: RunContext, session, account: str, start: str, end: str) -> CollectResult:
        path = self._write_statement(ctx, start, end)
        ctx.log(f"mock driver: 生成流水文件 {path.name}")
        return CollectResult(files=[path.resolve()])

    def collect_receipts(self, ctx: RunContext, session, account: str, start: str, end: str) -> CollectResult:
        target_dir = ctx.downloads_dir / "mock"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"mock_receipt_{dt.datetime.now().strftime('%H%M%S')}.txt"
        target.write_text(f"模拟回单\n账户: {account}\n期间: {start} ~ {end}\n", encoding="utf-8")
        ctx.log(f"mock driver: 生成回单文件 {target.name}")
        return CollectResult(files=[target.resolve()])
