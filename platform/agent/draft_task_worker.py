from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import subprocess
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from device_report import USER_AGENT, load_env, log, sleep_until_next, stop_requested

AGENT_VERSION = "draft-worker/1.0"
BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
MAX_FILE_BYTES = 20 * 1024 * 1024


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
    endpoint = url.rstrip("/") + "/api/agent/draft-tasks/claim"
    result = post_json(endpoint, token, {"agent_code": agent_code}, timeout=30)
    return result.get("task")


def download_file(url: str, token: str, task: dict, agent_code: str, target: Path) -> Path:
    relative = str(task.get("file_url") or "")
    if not relative:
        raise RuntimeError("任务没有制单文件")
    query = urllib.parse.urlencode({"agent_code": agent_code})
    endpoint = url.rstrip("/") + relative + "?" + query
    request = urllib.request.Request(
        endpoint,
        method="GET",
        headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def draft_command(args, bank_code: str, task_path: Path) -> tuple[list[str], Path]:
    fallback = ([args.collector_python, "run_draft.py", "--task", str(task_path)], Path(args.collector_root))
    path = Path(getattr(args, "collectors_file", "") or "")
    if not bank_code or not path.is_file():
        return fallback
    try:
        import yaml

        registry = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return fallback
    entry = registry.get(bank_code) or {}
    command = entry.get("draft_command") or ["python", "run_draft.py"]
    argv = [str(part) for part in command] if isinstance(command, list) else [str(command)]
    cwd = Path(str(entry.get("cwd") or args.collector_root))
    if not cwd.is_dir():
        raise RuntimeError(f"制单器工作目录不存在：{cwd}（请检查 collectors.yaml）")
    return [*argv, "--task", str(task_path)], cwd


def _terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _read_files(paths) -> list[dict]:
    files: list[dict] = []
    total = 0
    for raw in paths or []:
        path = Path(str(raw))
        if not path.is_file():
            continue
        content = path.read_bytes()
        total += len(content)
        if len(content) > MAX_FILE_BYTES or total > MAX_FILE_BYTES * 3:
            continue
        files.append({"name": path.name, "content_b64": base64.b64encode(content).decode("ascii")})
    return files


def execute_task(task: dict, args) -> dict:
    task_id = int(task["task_id"])
    attempt = int(task.get("attempt") or 1)
    work_dir = args.work_dir / f"draft_{task_id}_attempt_{attempt}"
    if work_dir.exists():
        import shutil

        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    file_name = task.get("file_name") or "platform_draft.xlsx"
    platform_file = download_file(args.url, args.token, task, args.agent_code, work_dir / file_name)
    result_path = work_dir / "result.json"
    job = dict(task)
    credentials = job.pop("credentials", None) or {}
    device = job.pop("device", None) or {}
    job.update(
        {
            "file_path": str(platform_file),
            "work_dir": str(work_dir),
            "result_path": str(result_path),
            "mb_exe": args.mb_exe,
            "gf3_exe": args.gf3_exe,
            "backend": device.get("backend", ""),
            "device_backend": device.get("backend", ""),
            "ter_id": device.get("ter_id", ""),
            "gf3_port": device.get("gf3_port", 0),
            "gf3_cabinet": device.get("gf3_cabinet", 0),
            "mb_user": args.mb_user or device.get("mb_user", ""),
        }
    )
    task_path = work_dir / "task.json"
    task_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.update(
        {
            "BANK_USERNAME": credentials.get("username", ""),
            "BANK_PASSWORD": credentials.get("password", ""),
            "BANK_PIN": credentials.get("pin", ""),
        }
    )
    started = time.time()
    exit_code: int | None = None
    log_text = ""
    timed_out = False
    try:
        argv, cwd = draft_command(args, task.get("bank_code", ""), task_path)
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}),
        )
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=2)
                exit_code = proc.returncode
                log_text = (stdout or "") + "\n" + (stderr or "")
                break
            except subprocess.TimeoutExpired:
                if time.time() - started > args.timeout or stop_requested():
                    timed_out = True
                    exit_code = 124
                    _terminate_process(proc)
                    stdout, stderr = proc.communicate()
                    log_text = ("制单子进程超时\n" if time.time() - started > args.timeout else "收到停止指令\n") + (stdout or "")
                    break
    except Exception:  # noqa: BLE001
        exit_code = 1
        log_text = traceback.format_exc()

    if result_path.exists():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            result = {"ok": False, "error_code": "UNKNOWN", "message": "结果文件无法解析"}
    elif timed_out:
        result = {"ok": False, "error_code": "TIMEOUT", "message": "制单子进程超时"}
    else:
        result = {"ok": False, "error_code": "UNKNOWN", "message": f"未生成结果文件 (exit={exit_code})"}
    files = _read_files(result.get("files"))
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
    endpoint = url.rstrip("/") + f"/api/agent/draft-tasks/{task_id}/result"
    return post_json(endpoint, token, payload, timeout=300)


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="制单任务 Agent")
    parser.add_argument("--url", default=os.getenv("PLATFORM_URL", ""))
    parser.add_argument("--token", default=os.getenv("PLATFORM_AGENT_TOKEN", ""))
    parser.add_argument("--agent-code", default=os.getenv("PLATFORM_AGENT_CODE", "local"))
    parser.add_argument("--mb-exe", default=os.getenv("PLATFORM_MB_EXE", "mb.exe"))
    parser.add_argument("--gf3-exe", default=os.getenv("PLATFORM_GF3_EXE", "gf3"))
    parser.add_argument("--mb-user", default=os.getenv("PLATFORM_MB_USER", ""))
    parser.add_argument("--collectors-file", default=os.getenv("PLATFORM_COLLECTORS_FILE", str(REPO_ROOT / "collectors.yaml")))
    parser.add_argument("--collector-python", default=os.getenv("PLATFORM_COLLECTOR_PYTHON", "python"))
    parser.add_argument("--collector-root", default=os.getenv("PLATFORM_COLLECTOR_ROOT", str(REPO_ROOT / "collectors")))
    parser.add_argument("--work-dir", default=os.getenv("PLATFORM_WORK_DIR", ""))
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    if not args.url or not args.token:
        log("缺少 PLATFORM_URL 或 PLATFORM_AGENT_TOKEN")
        return 1
    args.work_dir = Path(args.work_dir) if args.work_dir else Path.home() / ".ukeyagent" / "work" / "draft"
    args.work_dir.mkdir(parents=True, exist_ok=True)

    log(f"制单 Agent 启动：{args.agent_code} → {args.url}，版本 {AGENT_VERSION}")
    while not stop_requested():
        try:
            task = claim(args.url, args.token, args.agent_code)
        except Exception as exc:  # noqa: BLE001
            log(f"领取制单任务失败: {exc}")
            task = None
        if task:
            log(f"领取制单任务 {task.get('task_id')}: {task.get('bank_code')} {task.get('payer_account_no')}")
            try:
                payload = execute_task(task, args)
                report(args.url, args.token, int(task["task_id"]), payload)
                log(f"制单任务 {task.get('task_id')} 回传成功")
            except Exception as exc:  # noqa: BLE001
                log(f"制单任务执行/回传失败: {exc}")
        sleep_until_next(args.interval)
    log("收到停止指令，制单 Agent 退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
