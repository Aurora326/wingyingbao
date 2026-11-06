"""建设银行采集入口（平台调用）。

平台执行：python run_task.py --task task.json
日常手动：python main.py -s / -r  （照旧，不受影响）

契约：读 --task 指定的 task.json，把结果写到它的 result_path。
密钥不写进 task.json，由平台通过环境变量传入：BANK_USERNAME / BANK_PASSWORD / BANK_PIN。
制单不从这里触发：config.ini 的 payments.action 是 submit，平台入口必须显式关闭。
"""
import argparse
import json
import os
import sys
from pathlib import Path

CONTRACT_VERSION = 1
CREDENTIAL_ENV = {"username": "BANK_USERNAME", "password": "BANK_PASSWORD", "pin": "BANK_PIN"}


def _apply_task(automation, task: dict) -> None:
    """把平台任务里的区间、密钥和 UKey 设备信息覆盖到老配置上。"""
    config = automation.config

    start = str(task.get("start_date") or "")
    end = str(task.get("end_date") or "")
    if start and end:
        config.collection.statements.start_date = start
        config.collection.statements.end_date = end

    for key, env_name in CREDENTIAL_ENV.items():
        value = os.environ.get(env_name)
        if value:
            setattr(config.credentials, key, value)

    port = int(task.get("gf3_port") or 0)
    if port:
        config.cabinet.type = str(task.get("device_backend") or config.cabinet.type)
        config.cabinet.port = port
        config.cabinet.manual_mode = False
        if task.get("ter_id"):
            config.cabinet.ter_id = str(task["ter_id"])
        if task.get("mb_user"):
            config.cabinet.user = str(task["mb_user"])
        if task.get("mb_exe"):
            config.cabinet.mb_path = str(task["mb_exe"])


def main() -> int:
    parser = argparse.ArgumentParser(description="建设银行采集程序（平台入口）")
    parser.add_argument("--task", required=True, help="任务 JSON 文件路径")
    args = parser.parse_args()

    task_path = Path(args.task)
    task = json.loads(task_path.read_text(encoding="utf-8"))

    task_type = str(task.get("type") or "statements")
    result_path = Path(task.get("result_path") or (task_path.parent / "result.json"))
    result_path.parent.mkdir(parents=True, exist_ok=True)

    if task_type == "payments":
        payload = {
            "contract_version": CONTRACT_VERSION,
            "task_id": task.get("task_id"),
            "ok": False,
            "files": [],
            "no_data": False,
            "error_code": "NOT_IMPLEMENTED",
            "message": "制单不由平台采集任务触发",
        }
        result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1

    from main import CCBAutomation

    automation = CCBAutomation()
    _apply_task(automation, task)

    results = automation.run(
        collect_statements=task_type != "receipts",
        collect_receipts=task_type == "receipts",
        collect_payments=False,
    )

    files, no_data = [], False
    for item in list(results.get("statements") or []) + list(results.get("receipts") or []):
        if not item:
            continue
        path = Path(item)
        if "_无数据_" in path.name or path.name == "当前查询结果为空":
            no_data = True
            continue
        if path.is_file():
            files.append(str(path.resolve()))

    ok = bool(results.get("success"))
    payload = {
        "contract_version": CONTRACT_VERSION,
        "task_id": task.get("task_id"),
        "ok": ok,
        "files": files,
        "no_data": no_data,
        "error_code": "" if ok else "COLLECT_FAILED",
        "message": "" if ok else str(results.get("error") or "采集失败"),
    }

    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
