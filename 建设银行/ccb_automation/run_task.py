"""建设银行采集入口（平台调用）。

平台执行：python run_task.py --task task.json
日常手动：python main.py -s / -r  （照旧，不受影响）

契约：读 --task 指定的 task.json，把结果写到 result_path。
密钥不写进 task.json，由平台通过环境变量传入：BANK_USERNAME / BANK_PASSWORD / BANK_PIN。
制单不从这里触发：平台制单任务由 run_draft.py 执行。
"""
import argparse
import sys
from pathlib import Path

# 将项目根目录加入 sys.path，确保能引用 common 模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from common import task_runner


def main() -> int:
    parser = argparse.ArgumentParser(description="建设银行采集程序（平台入口）")
    parser.add_argument("--task", required=True, help="任务 JSON 文件路径")
    args = parser.parse_args()

    task_path = Path(args.task)
    task = task_runner.read_task(task_path)
    result_path = task_runner.result_path(task, task_path)
    task_type = str(task.get("type") or "statements")

    if task_type == "payments":
        task_runner.write_collect_result(
            result_path,
            task_id=task.get("task_id"),
            ok=False,
            error_code="NOT_IMPLEMENTED",
            message="制单不由平台采集任务触发",
        )
        return 1

    from main import CCBAutomation

    automation = CCBAutomation()
    task_runner.apply_task_overrides(automation.config, task)
    automation.diagnostics_dir = task_runner.diagnostics_dir(task, task_path)

    results = automation.run(
        collect_statements=task_type != "receipts",
        collect_receipts=task_type == "receipts",
        collect_payments=False,
    )

    files, no_data = task_runner.collect_outputs(results)
    ok = bool(results.get("success"))
    task_runner.write_collect_result(
        result_path,
        task_id=task.get("task_id"),
        ok=ok,
        files=files,
        no_data=no_data,
        error_code="COLLECT_FAILED",
        message="" if ok else str(results.get("error") or "采集失败"),
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
