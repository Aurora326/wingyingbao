# -*- coding: utf-8 -*-
"""
南京银行流水 -> 离线账户明细导入模板 转换工具

用法:
    python convert_statement_to_template.py
    python convert_statement_to_template.py --input 南京银行_银行流水_20260703.xlsx --output 已填模板_20260703.xls
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

# 引入项目本地依赖（避免污染系统 Python）
LIBS_DIR = Path(__file__).parent / ".pylibs"
if LIBS_DIR.exists():
    sys.path.insert(0, str(LIBS_DIR))

import xlrd
import xlwt
import openpyxl


TEMPLATE_PATH = Path(__file__).parent / "template" / "离线账户明细导入模板.xls"
DEFAULT_STATEMENT_DIR = Path(__file__).parent / "downloads" / "statements"


def find_latest_statement() -> Path:
    """按修改时间找到最新的银行流水文件"""
    candidates = list(DEFAULT_STATEMENT_DIR.rglob("南京银行_银行流水_*.xlsx"))
    if not candidates:
        raise FileNotFoundError(f"在 {DEFAULT_STATEMENT_DIR} 下未找到南京银行_银行流水_*.xlsx 文件")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def read_template_headers(template_path: Path) -> list:
    """读取模板表头（第 1 行）"""
    wb = xlrd.open_workbook(str(template_path))
    sheet = wb.sheet_by_index(0)
    headers = []
    for c in range(sheet.ncols):
        v = sheet.cell_value(0, c)
        headers.append('' if sheet.cell_type(0, c) == xlrd.XL_CELL_EMPTY else str(v))
    return headers


def read_statement(statement_path: Path) -> list[dict]:
    """读取南京银行流水文件，返回字段字典列表"""
    wb = openpyxl.load_workbook(str(statement_path), data_only=True)
    ws = wb.active
    rows = []
    header = None
    for idx, row in enumerate(ws.iter_rows(values_only=True)):
        if idx == 0:
            header = [str(c).strip() if c is not None else '' for c in row]
            continue
        if not row or all(c is None or str(c).strip() == '' for c in row):
            continue
        record = {h: row[i] if i < len(row) else None for i, h in enumerate(header)}
        rows.append(record)
    return rows


def parse_amount(value) -> float | None:
    """把金额字段转为 float；空或异常返回 None"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace(',', '').strip()
    if s == '':
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_date(value) -> str:
    """从交易时间提取日期（YYYY-MM-DD）"""
    if value is None:
        return ''
    s = str(value).strip()
    if not s:
        return ''
    # 支持 2026-06-26 05:06:04 或 2026/06/26
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    # 兜底：取前 10 个字符
    return s[:10]


def convert_currency(value) -> str:
    """币种映射：人民币 -> CNY 人民币"""
    if value is None:
        return ''
    s = str(value).strip()
    mapping = {
        "人民币": "CNY 人民币",
        "CNY": "CNY 人民币",
        "美元": "USD 美元",
        "USD": "USD 美元",
        "欧元": "EUR 欧元",
        "EUR": "EUR 欧元",
        "港币": "HKD 港币",
        "HKD": "HKD 港币",
        "日元": "JPY 日元",
        "JPY": "JPY 日元",
    }
    return mapping.get(s, s)


def convert_record(record: dict) -> list:
    """把一条南京银行流水记录映射为模板行"""
    direction = str(record.get('收支标识', '')).strip()
    amount = parse_amount(record.get('金额'))
    debit = amount if direction == '支出' else None
    credit = amount if direction == '收入' else None

    return [
        record.get('单位名称') or record.get('本方账号名') or '',  # 户名
        record.get('本方账号') or '',                              # 账号
        convert_currency(record.get('币种')),                      # 币种
        parse_date(record.get('交易时间')),                        # 交易日期
        debit,                                                     # 借方金额（支出）
        credit,                                                    # 贷方金额（收入）
        parse_amount(record.get('余额')),                          # 余额
        record.get('对方账号') or '',                              # 对方账号
        record.get('对方账户名称') or '',                          # 对方户名
        '',                                                        # 对方行号（南京银行流水无此字段）
        '',                                                        # 凭证号（南京银行流水无此字段）
        record.get('对方账户开户行') or '',                        # 对方行名
        record.get('摘要') or '',                                  # 交易类型
        record.get('附言') or '',                                  # 交易备注
        record.get('用途') or '',                                  # 备注
        '',                                                        # erp备注1
        '',                                                        # erp备注2
        '',                                                        # erp备注3
        '',                                                        # erp备注4
        '',                                                        # 合同号
    ]


def write_template(headers: list, rows: list[list], output_path: Path):
    """写入 .xls 模板文件"""
    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Sheet1')

    # 表头
    header_style = xlwt.easyxf('font: bold on;')
    for c, h in enumerate(headers):
        ws.write(0, c, h, header_style)

    # 数据
    for r, row in enumerate(rows, start=1):
        for c, val in enumerate(row):
            ws.write(r, c, val)

    # 自动列宽（粗略）
    for c, h in enumerate(headers):
        max_len = max(len(str(h)), max((len(str(row[c])) if row[c] is not None else 0 for row in rows), default=0))
        ws.col(c).width = min(256 * (max_len + 4), 12000)

    wb.save(str(output_path))


def convert(statement_path: Path | str, output_path: Path | str = None, template_path: Path | str = None) -> Path:
    """
    把南京银行流水文件转换为已填写的离线账户明细导入模板。

    Args:
        statement_path: 银行流水 xlsx 文件路径
        output_path: 输出 xls 文件路径（默认在流水文件同级目录自动生成）
        template_path: 模板 xls 路径（默认使用项目内置模板）

    Returns:
        生成的 xls 文件路径
    """
    statement_path = Path(statement_path)
    template_path = Path(template_path) if template_path else TEMPLATE_PATH

    if not template_path.exists():
        raise FileNotFoundError(f"模板文件不存在: {template_path}")
    if not statement_path.exists():
        raise FileNotFoundError(f"流水文件不存在: {statement_path}")

    if output_path:
        output_path = Path(output_path)
    else:
        date_str = datetime.now().strftime("%Y%m%d")
        output_path = statement_path.parent / f"离线账户明细导入_已填_{date_str}.xls"

    headers = read_template_headers(template_path)
    records = read_statement(statement_path)
    rows = [convert_record(r) for r in records]

    # 保证列数与模板一致
    for row in rows:
        while len(row) < len(headers):
            row.append('')
        row[:] = row[:len(headers)]

    write_template(headers, rows, output_path)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="南京银行流水转离线账户明细导入模板")
    parser.add_argument("--input", "-i", type=Path, help="银行流水 xlsx 文件路径（默认取最新的）")
    parser.add_argument("--output", "-o", type=Path, help="输出 xls 文件路径（默认在输入文件同级目录）")
    parser.add_argument("--template", "-t", type=Path, default=TEMPLATE_PATH, help="模板 xls 路径")
    args = parser.parse_args()

    statement_path = args.input or find_latest_statement()
    output_path = convert(
        statement_path=statement_path,
        output_path=args.output,
        template_path=args.template
    )
    print(f"成功生成: {output_path}")


if __name__ == "__main__":
    main()
