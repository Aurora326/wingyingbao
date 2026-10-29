from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.services.parsers.receipt import parse_receipts
from app.services.parsers.statement import dedupe_hash, parse_statement
from tests.conftest import REAL_CIB, REAL_CITIC

REAL_CITIC_RECEIPT = Path(r"C:\wangyingbao\中信网银\citic_automation\downloads\receipts\中信银行_电子回单_20260227.pdf")
REAL_CIB_RECEIPT = Path(r"C:\wangyingbao\兴业网银\cib_automation\downloads\receipts\兴业银行_电子回单_20260207.pdf")


def test_parse_citic_xlsx():
    assert REAL_CITIC.exists(), "缺少中信流水样本"
    parsed = parse_statement(REAL_CITIC)
    assert len(parsed.rows) == 1
    row = parsed.rows[0]
    assert parsed.account_no == "8110201012802044871"
    assert row.txn_date.isoformat() == "2026-02-09"
    assert row.txn_time == "14:15:41"
    assert row.direction == "credit"
    assert row.amount == Decimal("100.00")
    assert row.balance == Decimal("100.00")
    assert row.serial_no == "SC313413142513"


def test_parse_cib_zip():
    assert REAL_CIB.exists(), "缺少兴业流水样本"
    parsed = parse_statement(REAL_CIB)
    assert len(parsed.rows) == 21
    assert parsed.account_no
    first = parsed.rows[0]
    assert first.direction == "debit"
    assert first.amount == Decimal("200000.00")
    assert first.txn_time == "09:59:00"
    assert any(row.direction == "credit" for row in parsed.rows)


def test_dedupe_hash_stable():
    parsed = parse_statement(REAL_CITIC)
    row = parsed.rows[0]
    first = dedupe_hash("citic", parsed.account_no, row)
    second = dedupe_hash("citic", parsed.account_no, row)
    assert first == second
    assert first != dedupe_hash("cib", parsed.account_no, row)


def test_parse_rejects_unknown_format(tmp_path):
    target = tmp_path / "empty.xlsx"
    from openpyxl import Workbook

    Workbook().save(target)
    with pytest.raises(ValueError):
        parse_statement(target)


def test_parse_citic_receipt_pdf():
    assert REAL_CITIC_RECEIPT.exists(), "缺少中信回单样本"
    parsed = parse_receipts(REAL_CITIC_RECEIPT)
    assert len(parsed) == 1
    item = parsed[0]
    assert item.amount == Decimal("100.00")
    assert item.txn_date.isoformat() == "2026-02-09"
    assert item.receipt_no == "SC313413142513"
    assert item.counterparty_name == "上海隐石信息科技发展有限公司"


def test_parse_cib_receipt_pdf_splits_each_receipt():
    assert REAL_CIB_RECEIPT.exists(), "缺少兴业回单样本"
    parsed = parse_receipts(REAL_CIB_RECEIPT)
    assert len(parsed) == 7
    first = parsed[0]
    assert first.receipt_no == "202507040100101227"
    assert first.amount == Decimal("495360.00")
    assert first.txn_date.isoformat() == "2025-07-04"
    assert first.counterparty_name == "浙江华云信息科技有限公司"
    assert all(item.amount is not None and item.txn_date is not None for item in parsed)
    assert Decimal("10.89") in {item.amount for item in parsed}
