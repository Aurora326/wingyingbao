# -*- coding: utf-8 -*-
"""平台任务契约公共实现。

封装 task.json 读取、环境变量密钥注入、机柜参数覆盖、空数据识别、
result.json 写入，以及失败时调用 common.diagnostics 抓取现场。
各银行入口只保留“调起自己的 automation.run”。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONTRACT_VERSION = 1
CREDENTIAL_ENV = {"username": "BANK_USERNAME", "password": "BANK_PASSWORD", "pin": "BANK_PIN"}


def read_task(task_path: Path | str) -> dict:
    return json.loads(Path(task_path).read_text(encoding="utf-8"))


def result_path(task: dict, task_path: Path | str) -> Path:
    path = Path(task.get("result_path") or (Path(task_path).parent / "result.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def diagnostics_dir(task: dict, task_path: Path | str) -> Path:
    work_dir = Path(task.get("work_dir") or Path(task_path).parent)
    directory = Path(task.get("diagnostics_dir") or (work_dir / "diagnostics"))
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def apply_task_overrides(config: Any, task: dict) -> None:
    """把平台任务里的区间、密钥和 UKey 设备信息覆盖到本机配置。"""
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


def apply_draft_settings(config: Any, task: dict, items: list[dict], batch_file: Path) -> None:
    """制单任务的配置覆盖；平台制单固定 save 模式，不自动提交。"""
    payments = config.collection.payments
    payments.enabled = True
    payments.action = "save"
    payments.batch_file = str(batch_file)
    payments.items = items
    payments.payer_account = str(task.get("payer_account_no") or "")
    payments.payer_name = str(task.get("payer_name") or "")


def collect_outputs(results: dict) -> tuple[list[str], bool]:
    """汇总采集结果：存在的文件进 files，空数据标记/哨兵转 no_data。"""
    files: list[str] = []
    no_data = False
    for item in list(results.get("statements") or []) + list(results.get("receipts") or []):
        if not item:
            continue
        path = Path(item)
        if "_无数据_" in path.name or path.name == "当前查询结果为空":
            no_data = True
            continue
        if path.is_file():
            files.append(str(path.resolve()))
    return files, no_data


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_collect_result(
    path: Path,
    *,
    task_id: Any,
    ok: bool,
    files: list[str] | None = None,
    no_data: bool = False,
    error_code: str = "",
    message: str = "",
) -> None:
    _write(
        Path(path),
        {
            "contract_version": CONTRACT_VERSION,
            "task_id": task_id,
            "ok": ok,
            "files": files or [],
            "no_data": no_data,
            "error_code": "" if ok else (error_code or "COLLECT_FAILED"),
            "message": message,
        },
    )


def write_draft_result(
    path: Path,
    *,
    ok: bool,
    error_code: str = "",
    message: str = "",
    files: list[str] | None = None,
    bank_batch_no: str = "",
) -> None:
    _write(
        Path(path),
        {
            "contract_version": CONTRACT_VERSION,
            "ok": ok,
            "error_code": "" if ok else (error_code or "DRAFT_FAILED"),
            "message": message,
            "bank_batch_no": bank_batch_no,
            "files": files or [],
        },
    )


def capture_failure(
    page: Any,
    directory: Path | str,
    *,
    stage: str = "",
    error_code: str = "",
    message: str = "",
    steps: list[dict] | None = None,
) -> list[str]:
    """失败现场三件套；任何抓取异常都不覆盖原始业务异常。"""
    try:
        from common.diagnostics import capture_diagnostic

        return capture_diagnostic(
            page,
            directory,
            stage=stage,
            error_code=error_code,
            message=message,
            steps=steps,
        )
    except Exception:
        return []
