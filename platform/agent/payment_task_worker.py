from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from device_report import USER_AGENT, load_env, log, sleep_until_next, stop_requested

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
DEFAULT_COLLECTOR = REPO_ROOT.parent / "地产项目资料" / "采集明源云审批过程.py"


def _default_oa_collector() -> Path:
    candidates = (
        REPO_ROOT / "oa" / "采集OA付款数据.py",
        REPO_ROOT.parent / "地产项目资料" / "采集OA付款数据.py",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[-1]


DEFAULT_OA_COLLECTOR = _default_oa_collector()


def post_json(endpoint: str, token: str, payload: dict, timeout: int = 120) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"平台返回 {exc.code}: {detail}") from exc


def claim(url: str, token: str, agent_code: str) -> dict | None:
    endpoint = url.rstrip("/") + "/api/agent/payment-tasks/claim"
    result = post_json(endpoint, token, {"agent_code": agent_code}, timeout=30)
    return result.get("task")


def report(url: str, token: str, task_id: int, payload: dict) -> dict:
    endpoint = url.rstrip("/") + f"/api/agent/payment-tasks/{task_id}/result"
    return post_json(endpoint, token, payload, timeout=300)


def execute(task: dict, args) -> dict:
    task_id = int(task["task_id"])
    started = time.time()
    work_dir = args.work_dir / f"payment_task_{task_id}"
    work_dir.mkdir(parents=True, exist_ok=True)
    output = work_dir / (task.get("output_name") or f"payment_task_{task_id}.xlsx")
    if output.exists():
        output.unlink()

    source = task.get("source") or "mingyuan"
    collector = args.oa_collector if source == "oa" else args.collector
    if not collector.exists():
        return {
            "agent_code": args.agent_code,
            "attempt": 1,
            "exit_code": 1,
            "duration_ms": 0,
            "log_text": f"采集脚本不存在: {collector}",
            "result": {},
            "files": [],
        }

    if source == "oa":
        command = [
            sys.executable,
            str(collector),
            "--output",
            str(output),
            "--workers",
            "4",
        ]
        if task.get("since"):
            command += ["--since", str(task["since"])]
        skip_codes = task.get("skip_codes") or []
        if skip_codes:
            skip_file = work_dir / "skip_codes.txt"
            skip_file.write_text("\n".join(str(code) for code in skip_codes), encoding="utf-8")
            command += ["--skip-codes-file", str(skip_file)]
    else:
        command = [
            sys.executable,
            str(collector),
            "--limit",
            str(task.get("limit") or 0),
            "--output",
            str(output),
            "--force",
            "--concurrency",
            "5",
        ]
        if task.get("limit_nocontract") is not None and int(task["limit_nocontract"]) >= 0:
            command += ["--limit-nocontract", str(task["limit_nocontract"])]
        if task.get("since"):
            command += ["--since", str(task["since"])]

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    # 账号由平台在领取任务时下发，执行器不保存
    if source == "oa":
        if task.get("username"):
            env["OA_USERNAME"] = str(task["username"])
        if task.get("password"):
            env["OA_PASSWORD"] = str(task["password"])
    else:
        if task.get("username"):
            env["MY_USERNAME"] = str(task["username"])
        if task.get("password"):
            env["MY_PASSWORD"] = str(task["password"])
        if not env.get("MY_USERNAME") and env.get("MINGYUAN_USERNAME"):
            env["MY_USERNAME"] = env["MINGYUAN_USERNAME"]
        if not env.get("MY_PASSWORD") and env.get("MINGYUAN_PASSWORD"):
            env["MY_PASSWORD"] = env["MINGYUAN_PASSWORD"]

    log(f"领取付款采集任务 {task_id}: source={source} limit={task.get('limit')} since={task.get('since') or '-'}")
    lines: list[str] = []
    exit_code = 1
    timed_out = False
    started = time.time()

    def _pump(proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip()
            if line:
                lines.append(line)
                log(f"[采集] {line}")

    def _heartbeat() -> None:
        while not done.wait(30):
            log(f"[采集] 任务 {task_id} 进行中，已耗时 {int(time.time() - started)} 秒")

    done = threading.Event()
    heartbeat = threading.Thread(target=_heartbeat, daemon=True)
    heartbeat.start()
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(collector.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            bufsize=1,
        )
        pump = threading.Thread(target=_pump, args=(proc,), daemon=True)
        pump.start()
        try:
            exit_code = proc.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            exit_code = 124
        pump.join(timeout=5)
    except Exception as exc:  # noqa: BLE001
        log(f"采集子进程异常: {exc}")
        lines.append(f"采集子进程异常: {exc}")
    finally:
        done.set()

    log(f"[采集] 任务 {task_id} 结束，退出码 {exit_code}，耗时 {int(time.time() - started)} 秒")
    log_text = "\n".join(lines[-400:])
    if timed_out:
        log_text = f"采集子进程超时（{args.timeout}s）\n{log_text}"

    files = []
    if output.is_file():
        files.append(
            {
                "name": output.name,
                "content_b64": base64.b64encode(output.read_bytes()).decode("ascii"),
            }
        )
    return {
        "agent_code": args.agent_code,
        "attempt": 1,
        "exit_code": exit_code,
        "duration_ms": int((time.time() - started) * 1000),
        "log_text": log_text[-20000:],
        "result": {},
        "files": files,
    }


def run_once(args) -> int:
    try:
        task = claim(args.url, args.token, args.agent_code)
    except Exception as exc:  # noqa: BLE001
        log(f"领取付款采集任务失败: {exc}")
        return 2
    if not task:
        return 0
    payload = execute(task, args)
    try:
        result = report(args.url, args.token, int(task["task_id"]), payload)
        log(f"任务 {task['task_id']} 回传: {result.get('status')} {result.get('error_msg') or ''}")
        return 0
    except Exception as exc:  # noqa: BLE001
        log(f"任务 {task['task_id']} 回传失败: {exc}")
        return 1


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="明源云付款采集 Agent")
    parser.add_argument("--url", default=os.getenv("PLATFORM_URL", ""))
    parser.add_argument("--token", default=os.getenv("PLATFORM_AGENT_TOKEN", ""))
    parser.add_argument("--agent-code", default=os.getenv("PLATFORM_AGENT_CODE", "cust-1517"))
    parser.add_argument(
        "--collector",
        default=os.getenv("PLATFORM_MINGYUAN_COLLECTOR", str(DEFAULT_COLLECTOR)),
    )
    parser.add_argument(
        "--oa-collector",
        default=os.getenv("PLATFORM_OA_COLLECTOR", str(DEFAULT_OA_COLLECTOR)),
    )
    parser.add_argument(
        "--work-dir",
        default=os.getenv("PLATFORM_PAYMENT_WORK_DIR", str(BASE_DIR / "work" / "payment")),
    )
    parser.add_argument("--interval", type=int, default=int(os.getenv("PLATFORM_TASK_POLL_INTERVAL", "30")))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("PLATFORM_PAYMENT_TIMEOUT", "1800")))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    args.work_dir = Path(args.work_dir)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    args.collector = Path(args.collector)
    args.oa_collector = Path(args.oa_collector)
    if not args.url or not args.token:
        log("缺少 PLATFORM_URL 或 PLATFORM_AGENT_TOKEN")
        return 1
    if not args.collector.exists():
        log(f"采集脚本不存在: {args.collector}")
        return 1
    if not args.oa_collector.exists():
        log(f"OA 采集脚本不存在（OA 任务会失败）: {args.oa_collector}")
    log(f"付款采集 Agent 启动：{args.agent_code} → {args.url}")
    if args.once:
        return 0 if run_once(args) == 0 else 1
    while not stop_requested():
        run_once(args)
        sleep_until_next(max(5, args.interval))
    log("收到停止指令，付款采集 Agent 退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
