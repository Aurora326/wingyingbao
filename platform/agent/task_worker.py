from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from device_report import load_env, log

AGENT_VERSION = "task-worker/1.0"
BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 60 * 1024 * 1024


def post_json(endpoint: str, token: str, payload: dict, timeout: int = 120) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"平台返回 {exc.code}: {detail}") from exc


def claim(url: str, token: str, agent_code: str) -> dict | None:
    endpoint = url.rstrip("/") + "/api/agent/tasks/claim"
    result = post_json(endpoint, token, {"agent_code": agent_code}, timeout=30)
    return result.get("task")


def fetch_credentials(
    url: str, token: str, task_id: int, agent_code: str, attempt: int
) -> dict:
    query = urllib.parse.urlencode({"agent_code": agent_code, "attempt": attempt})
    endpoint = url.rstrip("/") + f"/api/agent/tasks/{task_id}/credentials?{query}"
    request = urllib.request.Request(
        endpoint,
        method="GET",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"平台返回 {exc.code}: {detail}") from exc


def _read_files(paths) -> list[dict]:
    files: list[dict] = []
    total = 0
    for raw in paths or []:
        path = Path(str(raw))
        if not path.is_file():
            continue
        content = path.read_bytes()
        total += len(content)
        if len(content) > MAX_FILE_BYTES:
            raise RuntimeError(f"单个文件超过 20MB，无法回传: {path.name}")
        if total > MAX_TOTAL_BYTES:
            raise RuntimeError("回传文件总大小超过 60MB")
        files.append(
            {
                "name": path.name,
                "content_b64": base64.b64encode(content).decode("ascii"),
            }
        )
    return files


def execute_task(task: dict, args) -> dict:
    task_id = int(task["task_id"])
    attempt = int(task.get("attempt") or 1)
    work_dir = args.work_dir / f"task_{task_id}_attempt_{attempt}"
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    (work_dir / "downloads").mkdir(parents=True, exist_ok=True)
    (work_dir / "logs").mkdir(parents=True, exist_ok=True)
    (work_dir / "archive").mkdir(parents=True, exist_ok=True)

    task_path = work_dir / "task.json"
    result_path = work_dir / "result.json"
    job = dict(task)
    # 密钥不进任务文件（它会留在客户机磁盘上），改由环境变量传给采集程序
    credentials = job.pop("credentials", None) or {}
    job.update(
        {
            "work_dir": str(work_dir),
            "profile_dir": str(work_dir / "profile"),
            "archive_root": str(work_dir / "archive"),
            "log_path": str(work_dir / "logs" / f"{task.get('bank_code', 'bank')}_{dt.date.today():%Y%m%d}.log"),
            "result_path": str(result_path),
            "mb_exe": args.mb_exe,
            "gf3_exe": args.gf3_exe,
            "mb_user": args.mb_user or task.get("mb_user", ""),
        }
    )
    task_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")

    started = time.time()
    exit_code: int | None = None
    log_text = ""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.update(
        {
            "BANK_USERNAME": credentials.get("username", ""),
            "BANK_PASSWORD": credentials.get("password", ""),
            "BANK_PIN": credentials.get("pin", ""),
        }
    )
    try:
        proc = subprocess.run(
            [args.collector_python, "-m", "bankcore.run_task", "--task", str(task_path)],
            cwd=str(args.collector_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=args.timeout,
            env=env,
        )
        exit_code = proc.returncode
        log_text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        log_text = f"采集子进程超时: {exc}"
    except Exception:  # noqa: BLE001
        exit_code = 1
        log_text = traceback.format_exc()

    if result_path.exists():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            result = {"error_code": "UNKNOWN", "message": "结果文件无法解析"}
    else:
        result = {"error_code": "UNKNOWN", "message": f"未生成结果文件 (exit={exit_code})"}

    files = _read_files(result.get("files"))
    result.pop("ok", None)
    return {
        "agent_code": args.agent_code,
        "attempt": attempt,
        "exit_code": exit_code,
        "duration_ms": int((time.time() - started) * 1000),
        "log_text": log_text,
        "result": result,
        "files": files,
    }


def report(url: str, token: str, task_id: int, payload: dict) -> dict:
    endpoint = url.rstrip("/") + f"/api/agent/tasks/{task_id}/result"
    return post_json(endpoint, token, payload, timeout=300)


def _error_payload(args, task: dict, code: str, message: str) -> dict:
    return {
        "agent_code": args.agent_code,
        "attempt": int(task.get("attempt") or 1),
        "exit_code": 1,
        "duration_ms": 0,
        "log_text": message,
        "result": {"error_code": code, "message": message},
        "files": [],
    }


def run_once(args) -> int:
    try:
        task = claim(args.url, args.token, args.agent_code)
    except Exception as exc:  # noqa: BLE001
        log(f"领取任务失败: {exc}")
        return 2
    if not task:
        return 0
    task_id = int(task["task_id"])
    log(f"领取任务 {task_id}: {task.get('bank_code')} {task.get('type')} {task.get('start_date')}~{task.get('end_date')}")
    try:
        task["credentials"] = fetch_credentials(
            args.url,
            args.token,
            task_id,
            args.agent_code,
            int(task.get("attempt") or 1),
        )
        payload = execute_task(task, args)
    except Exception as exc:  # noqa: BLE001
        payload = _error_payload(args, task, "TIMEOUT", f"客户机执行异常: {exc}")
    try:
        result = report(args.url, args.token, task_id, payload)
        log(
            f"任务 {task_id} 回传成功: status={result.get('status')} "
            f"error={result.get('error_code') or '-'}"
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        log(f"任务 {task_id} 回传失败: {exc}")
        return 1


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="客户侧采集任务执行 Agent")
    parser.add_argument("--url", default=os.getenv("PLATFORM_URL", ""))
    parser.add_argument("--token", default=os.getenv("PLATFORM_AGENT_TOKEN", ""))
    parser.add_argument("--agent-code", default=os.getenv("PLATFORM_AGENT_CODE", "cust-1517"))
    parser.add_argument("--mb-exe", default=os.getenv("PLATFORM_MB_EXE", "mb.exe"))
    parser.add_argument("--gf3-exe", default=os.getenv("PLATFORM_GF3_EXE", "gf3"))
    parser.add_argument("--mb-user", default=os.getenv("PLATFORM_MB_USER", ""))
    parser.add_argument(
        "--collector-python",
        default=os.getenv("PLATFORM_COLLECTOR_PYTHON", sys.executable),
    )
    parser.add_argument(
        "--collector-root",
        default=os.getenv("PLATFORM_COLLECTOR_ROOT", str(REPO_ROOT / "collectors")),
    )
    parser.add_argument(
        "--work-dir",
        default=os.getenv("PLATFORM_AGENT_WORK_DIR", str(BASE_DIR / "work")),
    )
    parser.add_argument("--interval", type=int, default=int(os.getenv("PLATFORM_TASK_POLL_INTERVAL", "15")))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("PLATFORM_TASK_TIMEOUT", "900")))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    args.work_dir = Path(args.work_dir)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    if not args.url or not args.token:
        log("缺少 PLATFORM_URL 或 PLATFORM_AGENT_TOKEN")
        return 1
    log(f"任务 Agent 启动：{args.agent_code} → {args.url}，版本 {AGENT_VERSION}")
    if args.once:
        return 0 if run_once(args) == 0 else 1
    while True:
        run_once(args)
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
