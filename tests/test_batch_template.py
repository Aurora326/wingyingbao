from __future__ import annotations

import json
from pathlib import Path

import xlrd
import xlwt

import batch_template


def _make_template(path: Path, header: list[str] | None = None) -> Path:
    workbook = xlwt.Workbook()
    sheet = workbook.add_sheet("Sheet1")
    for column, title in enumerate(header or ["a"] * 15):
        sheet.write(0, column, title)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(str(path))
    return path


def test_format_amount_variants():
    assert batch_template.format_amount("9,010") == "9010.00"
    assert batch_template.format_amount(5000) == "5000.00"
    assert batch_template.format_amount("abc") == "abc"


def test_load_mapping_fallback(tmp_path):
    fallback = batch_template.load_mapping(tmp_path / "missing.json")
    assert fallback == batch_template.DEFAULT_TEMPLATE_MAPPING

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert batch_template.load_mapping(broken) == batch_template.DEFAULT_TEMPLATE_MAPPING

    valid = tmp_path / "mapping.json"
    valid.write_text(json.dumps({"columns": [{"column": 1, "field": "payee_name"}]}), encoding="utf-8")
    assert batch_template.load_mapping(valid)["columns"][0]["field"] == "payee_name"


def test_mapped_value_precedence():
    config = {"payer_account": "DEFAULT-ACC", "notify": "1"}
    item = {"payer_account": "ITEM-ACC", "amount": "9,010"}
    seq_cfg = {"column": 1, "field": "_seq"}
    account_cfg = {"column": 2, "field": "payer_account", "default_config": "payer_account"}
    amount_cfg = {"column": 10, "field": "amount", "transform": "amount"}
    notify_cfg = {"column": 13, "field": "notify", "default_config": "notify", "default": "0"}

    assert batch_template.mapped_value(item, seq_cfg, 0, config) == "1"
    assert batch_template.mapped_value(item, account_cfg, 0, config) == "ITEM-ACC"
    assert batch_template.mapped_value({}, account_cfg, 0, config) == "DEFAULT-ACC"
    assert batch_template.mapped_value(item, amount_cfg, 0, config) == "9010.00"
    assert batch_template.mapped_value({}, notify_cfg, 0, config) == "1"


def test_generate_import_file_with_default_mapping(tmp_path):
    template = _make_template(tmp_path / "template.xls")
    items = [
        {
            "payer_account": "ITEM-ACC",
            "payer_name": "付款单位A",
            "payee_account": "6222000000000001",
            "payee_name": "收款单位A",
            "payee_bank_type": "",
            "payee_branch": "建设银行上海分行",
            "amount": "9,010",
            "purpose": "货款",
            "remark": "项目A",
            "notify": "",
        }
    ]
    config = {"payer_account": "DEFAULT-ACC", "payer_name": "默认付款单位", "notify": "0"}
    output = batch_template.generate_import_file(
        template, tmp_path / "out" / "import.xls", items, None, config
    )
    assert output.exists()

    book = xlrd.open_workbook(str(output))
    sheet = book.sheet_by_index(0)
    assert sheet.nrows >= 2
    assert sheet.cell_value(1, 0) == "1"                       # 序号
    assert sheet.cell_value(1, 1) == "ITEM-ACC"                # 付款账号取 item
    assert sheet.cell_value(1, 2) == "付款单位A"
    assert sheet.cell_value(1, 3) == "02"                      # 行别默认
    assert sheet.cell_value(1, 4) == "6222000000000001"
    assert sheet.cell_value(1, 5) == "收款单位A"
    assert sheet.cell_value(1, 9) == "9010.00"                 # 金额格式化
    assert sheet.cell_value(1, 12) == "0"                      # notify 取 config
    book.release_resources()


def test_generate_import_file_with_custom_mapping(tmp_path):
    template = _make_template(tmp_path / "template.xls", ["a", "b", "c"])
    mapping_file = tmp_path / "mapping.json"
    mapping_file.write_text(
        json.dumps(
            {
                "sheet": "Sheet1",
                "start_row": 2,
                "clear_until_row": 10,
                "clear_columns": 3,
                "columns": [
                    {"column": 1, "field": "_seq"},
                    {"column": 2, "field": "payee_account"},
                    {"column": 3, "field": "amount", "transform": "amount"},
                ],
            }
        ),
        encoding="utf-8",
    )
    items = [{"payee_account": "ACC-1", "amount": "100"}]
    output = batch_template.generate_import_file(
        template, tmp_path / "import_custom.xls", items, mapping_file, {}
    )

    book = xlrd.open_workbook(str(output))
    sheet = book.sheet_by_index(0)
    assert sheet.cell_value(1, 0) == "1"
    assert sheet.cell_value(1, 1) == "ACC-1"
    assert sheet.cell_value(1, 2) == "100.00"
    book.release_resources()


def test_generate_import_file_missing_template_raises(tmp_path):
    try:
        batch_template.generate_import_file(
            tmp_path / "missing.xls", tmp_path / "out.xls", [], None, {}
        )
    except batch_template.TemplateError as exc:
        assert "模板不存在" in str(exc)
    else:
        raise AssertionError("缺失模板应抛 TemplateError")
