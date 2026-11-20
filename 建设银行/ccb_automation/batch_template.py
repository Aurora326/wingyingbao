# -*- coding: utf-8 -*-
"""建行批量转账模板：字段映射与 .xls 导入文件生成（无浏览器依赖）。"""
from __future__ import annotations

import json
import shutil
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List

from loguru import logger

try:
    import xlrd
    from xlutils.copy import copy as xlutils_copy

    XL_UTILS_AVAILABLE = True
except Exception:
    XL_UTILS_AVAILABLE = False
    logger.warning("xlrd/xlwt/xlutils 未安装，.xls 模板处理可能不可用")


class TemplateError(RuntimeError):
    """模板/依赖异常，由调用方转成制单失败结果。"""


DEFAULT_TEMPLATE_MAPPING = {
    "sheet": "Sheet1",
    "start_row": 2,          # 数据从第 2 行开始（1-based，对应 xlwt row 1）
    "clear_until_row": 3002,  # 最多支持 3000 笔
    "clear_columns": 15,
    "columns": [
        {"column": 1, "field": "_seq", "default": ""},          # 序号（自动生成）
        {"column": 2, "field": "payer_account", "default_config": "payer_account"},
        {"column": 3, "field": "payer_name", "default_config": "payer_name"},
        {"column": 4, "field": "payee_bank_type", "default_config": "payee_bank_type", "default": "02"},
        {"column": 5, "field": "payee_account"},
        {"column": 6, "field": "payee_name"},
        {"column": 7, "field": "payee_branch", "default_config": "payee_branch"},
        {"column": 8, "field": "payee_bank_code", "default_config": "payee_bank_code"},
        {"column": 9, "field": "serial_no", "default_config": "serial_no"},
        {"column": 10, "field": "amount", "transform": "amount"},
        {"column": 11, "field": "purpose", "default_config": "purpose"},
        {"column": 12, "field": "remark", "default_config": "remark"},
        {"column": 13, "field": "notify", "default_config": "notify", "default": "0"},
        {"column": 14, "field": "payee_phone", "default_config": "payee_phone"},
        {"column": 15, "field": "notify_msg", "default_config": "notify_msg"},
    ],
}


def format_amount(value: Any) -> str:
    try:
        return f"{Decimal(str(value).replace(',', '').strip()):.2f}"
    except (InvalidOperation, ValueError):
        return str(value or "").strip()


def amount_decimal(value: Any) -> Decimal:
    try:
        return Decimal(format_amount(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def load_mapping(mapping_file: Path | None) -> Dict[str, Any]:
    """读取模板映射；文件不存在或格式错误时回退内置映射。"""
    path = Path(mapping_file) if mapping_file else None
    if path is None or not path.exists():
        logger.warning(f"批量模板映射文件不存在，使用内置映射: {path}")
        return dict(DEFAULT_TEMPLATE_MAPPING)
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            mapping = json.load(f)
        if not isinstance(mapping, dict) or not mapping.get("columns"):
            raise ValueError("mapping must be an object with columns")
        return mapping
    except Exception as e:
        logger.warning(f"加载批量模板映射失败，使用内置映射: {path}, {e}")
        return dict(DEFAULT_TEMPLATE_MAPPING)


def mapped_value(item: Dict, column_cfg: Dict[str, Any], row_index: int, config: Dict) -> str:
    fields = column_cfg.get("field", [])
    if isinstance(fields, str):
        fields = [fields]

    value = ""
    for field in fields:
        if field == "_seq":
            value = str(row_index + 1)
            break
        value = item.get(field, "")
        if value not in ("", None):
            break

    default_config = column_cfg.get("default_config")
    if value in ("", None) and default_config:
        value = config.get(default_config, "")
    if value in ("", None):
        value = column_cfg.get("default", "")

    transform = column_cfg.get("transform")
    if transform == "amount":
        return format_amount(value)
    return str(value or "")


def generate_import_file(
    template_path: Path,
    output_path: Path,
    items: List[Dict],
    mapping_file: Path | None,
    config: Dict,
) -> Path:
    """按建行批量转账模板生成导入文件，返回输出路径。"""
    template_path = Path(template_path)
    output_path = Path(output_path)
    if not template_path.exists():
        raise TemplateError(f"批量转账模板不存在: {template_path}")
    if not XL_UTILS_AVAILABLE:
        raise TemplateError("缺少 xlrd/xlwt/xlutils，无法处理 .xls 模板。请执行: pip install xlrd xlwt xlutils")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Windows 下 xlrd 打开文件后会保持句柄，导致 xlwt 无法覆写同一文件。
    # 策略：复制模板到临时文件 → xlrd 读取临时文件 → xlutils.copy 修改 → 保存到目标文件
    with tempfile.NamedTemporaryFile(suffix=".xls", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    shutil.copy2(template_path, tmp_path)

    try:
        rb = xlrd.open_workbook(str(tmp_path), formatting_info=True)
        wb = xlutils_copy(rb)
        mapping = load_mapping(mapping_file)
        sheet_name = mapping.get("sheet") or "Sheet1"

        sheet_idx = rb.sheet_names().index(sheet_name) if sheet_name in rb.sheet_names() else 0
        ws = wb.get_sheet(sheet_idx)

        start_row = int(mapping.get("start_row") or 2)  # 1-based
        clear_until_row = int(mapping.get("clear_until_row") or 3002)
        clear_columns = int(mapping.get("clear_columns") or 15)
        columns = mapping.get("columns") or DEFAULT_TEMPLATE_MAPPING["columns"]

        # 清空旧数据（保留表头）
        for row in range(start_row, clear_until_row + 1):
            for col in range(1, clear_columns + 1):
                try:
                    ws.write(row - 1, col - 1, "")
                except Exception:
                    pass

        total = Decimal("0")
        for offset, item in enumerate(items):
            row = start_row + offset  # 1-based Excel row
            amount = format_amount(item.get("amount", ""))
            try:
                total += Decimal(amount)
            except (InvalidOperation, ValueError):
                pass
            for column_cfg in columns:
                col = int(column_cfg["column"])  # 1-based
                val = mapped_value(item, column_cfg, offset, config)
                ws.write(row - 1, col - 1, val)

        # 保存到目标路径（先关闭 rb 释放句柄）
        rb.release_resources()
        del rb
        wb.save(str(output_path))
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass

    logger.info(f"已生成批量转账导入文件: {output_path}，笔数={len(items)}，总金额={total:.2f}")
    return output_path
