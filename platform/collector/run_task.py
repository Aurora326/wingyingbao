from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import signal
import sys
import traceback
from pathlib import Path

from collector.core.archive import cleanup_old_archives
from collector.core.result import CollectResult, ErrorCode
from collector.drivers.base import RunContext
from collector.drivers.registry import get_driver

# 与 app/services/collectors.py 的 CONTRACT_VERSION 对应。两侧独立发版，
# 版本不一致时平台会在任务日志里点出来。
CONTRACT_VERSION = 1

CREDENTIAL_ENV = {"username": "BANK_USERNAME", "password": "BANK_PASSWORD", "pin": "BANK_PIN"}


def apply_credentials(task: dict) -> None:
    """密钥不写进 task.json（它会留在磁盘上），由平台通过环境变量传入。"""
    credentials = dict(task.get("credentials") or {})
    for key, name in CREDENTIAL_ENV.items():
        value = os.environ.get(name)
        if value is not None:
            credentials[key] = value
    task["credentials"] = credentials



def build_logger(log_path: Path, extra_path: Path | None = None) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("collector")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    for target in (log_path, extra_path):
        if target is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(target, encoding="utf-8")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger


def execute(task: dict) -> CollectResult:
    bank_code = task["bank_code"]
    work_dir = Path(task["work_dir"])
    work_dir.mkdir(parents=True, exist_ok=True)
    extra_log = Path(task["log_path"]) if task.get("log_path") else None
    logger = build_logger(work_dir / "collector.log", extra_log)

    driver = get_driver(bank_code, task.get("profile_path"))
    ctx = RunContext(
        task=task,
        profile=driver.profile,
        work_dir=work_dir,
        downloads_dir=work_dir / "downloads",
        archive_root=Path(task["archive_root"]),
        logger=logger,
        profile_dir=Path(task["profile_dir"]) if task.get("profile_dir") else None,
    )
    ctx.downloads_dir.mkdir(parents=True, exist_ok=True)

    retention = int(driver.option("retention_months", 0) or 0)
    if retention:
        removed = cleanup_old_archives(ctx.archive_root, retention)
        if removed:
            logger.info("清理过期归档目录 %s 个", removed)

    state = {"cleaned": False}

    def cleanup() -> None:
        if state["cleaned"]:
            return
        state["cleaned"] = True
        try:
            driver.teardown(ctx)
            logger.info("已释放设备资源")
        except Exception as exc:  # noqa: BLE001
            logger.warning("释放设备资源失败: %s", exc)

    atexit.register(cleanup)
    for sig in (signal.SIGINT, getattr(signal, "SIGBREAK", None)):
        if sig is None:
            continue
        try:
            signal.signal(sig, lambda *_: sys.exit(130))
        except Exception:
            pass

    try:
        logger.info("开始采集: bank=%s account=%s type=%s", bank_code, task.get("account_no"), task.get("type"))
        driver.prepare(ctx)
        logger.info("启动浏览器...")
        session = driver.start_browser(ctx) if driver.needs_browser else None
        if session is not None:
            logger.info("浏览器已启动")
        if not driver.login(ctx, session):
            return CollectResult(error_code=ErrorCode.LOGIN_FAILED, message="登录失败，请检查 UKey 与凭据")
        logger.info("登录成功")
        start = task["start_date"]
        end = task["end_date"]
        account = task.get("account_no", "")
        if task["type"] == "statements":
            return driver.collect_statements(ctx, session, account, start, end)
        if task["type"] == "reconciliation":
            return driver.collect_reconciliation(ctx, session, account, start, end)
        return driver.collect_receipts(ctx, session, account, start, end)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("采集异常: %s\n%s", exc, traceback.format_exc())
        return CollectResult(error_code=ErrorCode.UNKNOWN, message=f"{type(exc).__name__}: {exc}")
    finally:
        cleanup()
        logger.info("采集结束")


def main() -> int:
    parser = argparse.ArgumentParser(description="统一银行采集任务执行器")
    parser.add_argument("--task", required=True, help="任务 JSON 文件路径")
    args = parser.parse_args()

    task_path = Path(args.task)
    task = json.loads(task_path.read_text(encoding="utf-8"))
    apply_credentials(task)
    result = execute(task)

    result_path = Path(task.get("result_path") or (task_path.parent / "result.json"))
    result_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "contract_version": CONTRACT_VERSION,
        "task_id": task.get("task_id"),
        "ok": result.ok,
        **result.to_dict(),
    }
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
