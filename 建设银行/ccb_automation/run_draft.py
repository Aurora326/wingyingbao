# -*- coding: utf-8 -*-
"""建设银行制单入口（平台调用）。

平台下发批量 Excel（付款协同生成），这里转换成建行批量转账模板并执行制单。
当前阶段固定 save 模式：上传完成后停在「下一步」前，不提交。
"""
import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

# 将项目根目录加入 sys.path，确保能引用 common 模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from openpyxl import load_workbook

CONTRACT_VERSION = 1


def _bank_type(branch: str) -> str:
    return "01" if "建设银行" in (branch or "") else "02"


def _amount(value) -> str:
    try:
        return f"{Decimal(str(value)):.2f}"
    except (InvalidOperation, ValueError):
        return "0.00"


def load_items(path: Path, task: dict) -> list[dict]:
    wb = load_workbook(path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(cell.value).strip() if cell.value is not None else "" for cell in ws[1]]
    index = {name: i for i, name in enumerate(headers)}

    def cell(row, name: str, default=""):
        position = index.get(name)
        if position is None or position >= len(row) or row[position] in (None, ""):
            return default
        return row[position]

    items = []
    for row in rows[1:]:
        if not row or all(value in (None, "") for value in row):
            continue
        payee_bank = str(cell(row, "收款开户行", "")).strip()
        items.append(
            {
                "payer_account": str(cell(row, "付款账户", task.get("payer_account_no", ""))).strip(),
                "payer_name": str(cell(row, "付款单位", task.get("payer_name", ""))).strip(),
                "payee_bank_type": _bank_type(payee_bank),
                "payee_account": str(cell(row, "收款账号", "")).strip(),
                "payee_name": str(cell(row, "收款单位", "")).strip(),
                "payee_branch": payee_bank,
                "payee_bank_code": "",
                "amount": _amount(cell(row, "金额", "")),
                "purpose": str(cell(row, "用途", "")).strip(),
                "remark": str(cell(row, "项目", "")).strip(),
                "notify": "0",
            }
        )
    return items


def write_result(path: Path, ok: bool, code: str, message: str, files: list[str] | None = None) -> None:
    payload = {
        "contract_version": CONTRACT_VERSION,
        "ok": ok,
        "error_code": "" if ok else code,
        "message": message,
        "bank_batch_no": "",
        "files": files or [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="建设银行制单程序（平台入口）")
    parser.add_argument("--task", required=True, help="任务 JSON 文件路径")
    args = parser.parse_args()

    task_path = Path(args.task)
    task = json.loads(task_path.read_text(encoding="utf-8"))
    result_path = Path(task.get("result_path") or (task_path.parent / "result.json"))
    work_dir = Path(task.get("work_dir") or task_path.parent)

    source = Path(str(task.get("file_path") or ""))
    if not source.is_file():
        write_result(result_path, False, "FILE_MISSING", f"平台制单文件不存在：{source}")
        return 1

    items = load_items(source, task)
    if not items:
        write_result(result_path, False, "NO_ITEMS", "平台制单文件里没有可制单的单据")
        return 1

    batch_json = work_dir / "batch_payments.json"
    batch_json.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        from main import CCBAutomation
        from run_task import _apply_task

        automation = CCBAutomation()
        config = automation.config
        config.collection.payments.enabled = True
        config.collection.payments.action = "save"
        config.collection.payments.batch_file = str(batch_json)
        # 配置加载时可能已预加载旧 items，这里必须用平台解析出的清单覆盖
        config.collection.payments.items = items
        _apply_task(automation, task)

        results = automation.run(
            collect_statements=False,
            collect_receipts=False,
            collect_payments=True,
        )
        payments = results.get("payments") or {}
        failed = any(str(value).startswith("失败:") for value in payments.values())
        ok = bool(results.get("success")) and not failed and bool(payments)
        message = "; ".join(f"{key}: {value}" for key, value in payments.items()) or "制单未返回结果"
        files: list[str] = []
        debug_dir = Path(__file__).resolve().parent / "debug"
        shots = sorted(debug_dir.glob("*_制单提交前.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        if shots:
            files.append(str(shots[0]))
        write_result(result_path, ok, "DRAFT_FAILED", message, files)
        return 0 if ok else 1
    except Exception as exc:  # noqa: BLE001
        write_result(result_path, False, "DRAFT_EXCEPTION", f"{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
