from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Artifact, AuditLog, BankAccount, CollectionTask, Credential, TaskRun, UkeyDevice, utcnow
from app.services import ingest as ingest_service
from app.services import collectors, security, storage
from app.services.agents import heartbeat
from app.services.lock import BusLock
from app.services.parsers import statement as statement_parser

RETRY_LIMIT = 3
SUBPROCESS_TIMEOUT = 900


def advance_watermark(account, end_date, no_data: bool) -> None:
    """只有真正取到数据才推进水位。查询为空时保持不变，
    否则区间会永远收缩在最近几天，历史数据再也取不回来。"""
    if not no_data:
        account.watermark_date = end_date


@dataclass
class ClaimedTask:
    id: int
    attempt: int


def claim_task(db, agent_code: str | None = None) -> ClaimedTask | None:
    now = utcnow()
    stale = db.execute(
        select(CollectionTask).where(
            CollectionTask.status == "leased",
            CollectionTask.lease_until.isnot(None),
            CollectionTask.lease_until < now,
        )
    ).scalars().all()
    for task in stale:
        if task.cancel_requested:
            task.status = "cancelled"
            task.error_code = "CANCELLED"
            task.error_msg = "用户停止任务"
        elif task.attempts < RETRY_LIMIT:
            task.status = "queued"
            task.error_code = "TIMEOUT"
            task.error_msg = "执行进程中断，任务已重新排队"
        else:
            task.status = "failed"
            task.error_code = "TIMEOUT"
            task.error_msg = "执行进程中断且超过重试次数"
        task.lease_until = None
    if stale:
        db.commit()

    query = select(CollectionTask).where(CollectionTask.status == "queued")
    if agent_code:
        query = query.where(CollectionTask.agent_code == agent_code)
    task = db.execute(query.order_by(CollectionTask.priority, CollectionTask.created_at).limit(1)).scalars().first()
    if task is None:
        return None
    task.status = "leased"
    task.attempts += 1
    task.lease_until = utcnow() + dt.timedelta(minutes=30)
    task.started_at = utcnow()
    db.commit()
    return ClaimedTask(id=task.id, attempt=task.attempts)


def _build_task_payload(db, task: CollectionTask) -> dict:
    account = db.get(BankAccount, task.account_id)
    credential = db.execute(select(Credential).where(Credential.bank_account_id == account.id)).scalars().first()
    device = db.get(UkeyDevice, account.ukey_device_id) if account.ukey_device_id else None
    work_dir = settings.tasks_dir / f"task_{task.id}_attempt_{task.attempts}"
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = settings.profiles_dir / f"{account.bank_code}_{account.account_no}".replace(" ", "_")
    profile_dir.mkdir(parents=True, exist_ok=True)
    extra = device.extra or {} if device else {}
    return {
        "task_id": task.id,
        "attempt": task.attempts,
        "contract_version": collectors.CONTRACT_VERSION,
        "bank_code": account.bank_code,
        "account_no": account.account_no,
        "account_name": account.account_name,
        "type": task.type,
        "start_date": task.start_date.isoformat(),
        "end_date": task.end_date.isoformat(),
        "credentials": {
            "username": security.decrypt(credential.username_enc) if credential else "",
            "password": security.decrypt(credential.password_enc) if credential else "",
            "pin": security.decrypt(credential.pin_enc) if credential else "",
        },
        "options": account.driver_options or {},
        "gf3_cabinet": device.gf3_cabinet if device else 0,
        "gf3_port": device.gf3_port if device else 0,
        "gf3_exe": settings.gf3_exe,
        "mb_exe": settings.mb_exe,
        "mb_user": extra.get("mb_user") or settings.mb_user,
        "ter_id": extra.get("ter_id", ""),
        "device_id": device.id if device else 0,
        "device_backend": device.backend if device else "",
        "agent_code": task.agent_code or settings.agent_code,
        "work_dir": str(work_dir),
        "profile_dir": str(profile_dir),
        "archive_root": str(settings.artifacts_dir),
        "log_path": str(settings.logs_dir / f"{account.bank_code}_{dt.date.today():%Y%m%d}.log"),
        "result_path": str(work_dir / "result.json"),
    }


ARTIFACT_KINDS = {"statements": "statement", "receipts": "receipt", "reconciliation": "reconciliation"}


def _register_artifact(db, path: Path, task: CollectionTask, account: BankAccount) -> Artifact | None:
    path = Path(path)
    if not path.exists() or not path.is_file():
        return None
    root = settings.artifacts_dir.resolve()
    try:
        relative = path.resolve().relative_to(root)
        object_key = str(relative).replace("\\", "/")
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        size = len(content)
        original_name = path.name
    except ValueError:
        info = storage.save_file(path, account.bank_code, account.account_no)
        object_key = info["object_key"]
        digest = info["sha256"]
        size = info["size"]
        original_name = info["original_name"]
    artifact = Artifact(
        task_run_id=None,
        account_id=account.id,
        kind=ARTIFACT_KINDS.get(task.type, task.type),
        object_key=object_key,
        original_name=original_name,
        sha256=digest,
        size=size,
        content_type=Path(object_key).suffix.lstrip("."),
        parsed_status="pending",
        tenant_id=account.tenant_id,
    )
    db.add(artifact)
    db.flush()
    return artifact


def _finalize(db, task: CollectionTask, result: dict, exit_code: int | None, log_text: str, duration_ms: int) -> None:
    account = db.get(BankAccount, task.account_id)
    run = TaskRun(
        task_id=task.id,
        attempt=task.attempts,
        agent_id=task.agent_code or settings.agent_code,
        exit_code=exit_code,
        log_text=log_text[-20000:],
        duration_ms=duration_ms,
        tenant_id=task.tenant_id,
    )
    db.add(run)
    db.flush()

    error_code = result.get("error_code") or ""
    cancelled = bool(task.cancel_requested) or error_code == "CANCELLED"
    no_data = bool(result.get("no_data"))
    files = [Path(p) for p in result.get("files", [])]

    artifacts: list[Artifact] = []
    for path in files:
        artifact = _register_artifact(db, path, task, account)
        if artifact is not None:
            artifact.task_run_id = run.id
            artifacts.append(artifact)

    ingest_summary = {"inserted": 0, "duplicates": 0, "failed": 0, "errors": []}
    for artifact in artifacts:
        path = settings.artifacts_dir / artifact.object_key
        if task.type not in ("statements", "receipts"):
            ingest_summary["errors"].append(f"{artifact.original_name}: {task.type} 暂未实现解析，已保存原文件")
            continue
        try:
            if task.type == "statements":
                outcome = ingest_service.ingest_statement(db, account, path, artifact.id)
            else:
                outcome = ingest_service.ingest_receipt(db, account, path, artifact.id, artifact.object_key)
            artifact.parsed_status = "ok" if outcome.failed == 0 else "failed"
            artifact.parse_error = "; ".join(outcome.errors)[:1000]
            ingest_summary["inserted"] += outcome.inserted
            ingest_summary["duplicates"] += outcome.duplicates
            ingest_summary["failed"] += outcome.failed
            ingest_summary["errors"].extend(outcome.errors)
        except Exception as exc:  # noqa: BLE001
            artifact.parsed_status = "failed"
            artifact.parse_error = f"{type(exc).__name__}: {exc}"[:1000]
            ingest_summary["failed"] += 1

    if task.type == "statements":
        continuous, breaks = ingest_service.verify_balance_continuity(db, account.id)
        if not continuous:
            ingest_summary["errors"].extend(breaks[:5])

    task.finished_at = utcnow()
    if cancelled:
        task.status = "cancelled"
        task.error_code = "CANCELLED"
        task.error_msg = result.get("message", "用户停止任务")[:500]
    elif error_code:
        if error_code in ("TIMEOUT", "DOWNLOAD_FAILED", "DEVICE_BUSY") and task.attempts < RETRY_LIMIT:
            task.status = "queued"
            task.lease_until = None
            task.error_code = error_code
            task.error_msg = result.get("message", "")[:500]
        else:
            task.status = "failed"
            task.error_code = error_code
            task.error_msg = result.get("message", "")[:500]
    else:
        task.status = "succeeded"
        task.error_code = "NO_DATA" if no_data else ""
        task.error_msg = "查询结果为空" if no_data else ""
        # 查询为空不代表数据已补齐（可能是日期没生效或临时无数据），
        # 此时推进水位会让区间永远收缩在最近几天，历史数据再也取不到
        advance_watermark(account, task.end_date, no_data)

    db.add(
        AuditLog(
            actor="worker",
            action="task_finished",
            target_type="collection_task",
            target_id=str(task.id),
            detail={
                "status": task.status,
                "error_code": task.error_code,
                "ingest": ingest_summary,
                "artifacts": [a.object_key for a in artifacts],
            },
            tenant_id=task.tenant_id,
        )
    )
    db.commit()


def _release_device(payload: dict) -> None:
    """任务结束后强制归还机柜端口（子进程崩溃/被杀时也能兜底）"""
    backend = str(payload.get("device_backend") or "").lower()
    port = int(payload.get("gf3_port") or 0)
    if not port:
        return
    if backend == "mb":
        from app.services.device.mb import MbController

        try:
            MbController(
                exe=payload.get("mb_exe", ""),
                ter_id=str(payload.get("ter_id") or ""),
                port=port,
                user=str(payload.get("mb_user") or ""),
            ).disconnect()
        except Exception:
            pass
    elif backend == "gf3":
        from app.services.device.gf3 import Gf3Controller

        try:
            Gf3Controller(exe=payload.get("gf3_exe", "gf3"), cabinet=int(payload.get("gf3_cabinet") or 1)).disconnect(port)
        except Exception:
            pass


def _terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            errors="replace",
        )
        return
    try:
        os.killpg(os.getpgid(proc.pid), 15)
    except Exception:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def run_task(task_id: int) -> None:
    db = SessionLocal()
    try:
        task = db.get(CollectionTask, task_id)
        if task is None:
            return
        account = db.get(BankAccount, task.account_id)
        payload = _build_task_payload(db, task)
        # 密钥不写进任务文件：task.json 会留在磁盘上供排查，而 PIN 是长久的
        credentials = payload.pop("credentials", {})
        task_file = Path(payload["work_dir"]) / "task.json"
        task_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

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
        result: dict = {}
        cancelled = False
        timed_out = False
        log_text = ""
        lock = BusLock(settings.bus_lock_path)
        try:
            command = collectors.resolve(payload["bank_code"])
            log_text = collectors.describe(command, task_file)
            if not command.cwd.is_dir():
                raise FileNotFoundError(f"采集程序目录不存在: {command.cwd}")
            try:
                db.refresh(task)
            except Exception:
                pass
            if task.cancel_requested:
                cancelled = True
                exit_code = 130
                result = {"error_code": "CANCELLED", "message": "用户停止任务"}
                log_text += "\n用户停止任务"
            else:
                lock.acquire()
                popen_kwargs = (
                    {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                    if os.name == "nt"
                    else {"start_new_session": True}
                )
                proc = subprocess.Popen(
                    [*command.argv, "--task", str(task_file)],
                    cwd=str(command.cwd),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    **popen_kwargs,
                )
                last_cancel_check = 0.0
                while True:
                    try:
                        stdout, stderr = proc.communicate(timeout=2)
                        exit_code = proc.returncode
                        log_text += "\n" + (stdout or "") + "\n" + (stderr or "")
                        break
                    except subprocess.TimeoutExpired:
                        now = time.time()
                        if now - started > SUBPROCESS_TIMEOUT:
                            timed_out = True
                            exit_code = 124
                            result = {"error_code": "TIMEOUT", "message": "采集子进程超时"}
                            _terminate_process(proc)
                            stdout, stderr = proc.communicate()
                            log_text += "\n采集子进程超时\n" + (stdout or "") + "\n" + (stderr or "")
                            break
                        if now - last_cancel_check >= 3:
                            last_cancel_check = now
                            try:
                                db.refresh(task)
                            except Exception:
                                pass
                            if task.cancel_requested:
                                cancelled = True
                                exit_code = 130
                                result = {"error_code": "CANCELLED", "message": "用户停止任务"}
                                _terminate_process(proc)
                                stdout, stderr = proc.communicate()
                                log_text += "\n用户停止任务\n" + (stdout or "") + "\n" + (stderr or "")
                                break
        except (collectors.CollectorNotConfigured, FileNotFoundError) as exc:
            # 清单没配、或指向的目录/程序不存在，属于部署问题，重试无用
            result = {"error_code": "UNKNOWN", "message": str(exc)}
            log_text += f"\n{exc}"
        except Exception as exc:  # noqa: BLE001
            result = {"error_code": "UNKNOWN", "message": f"{type(exc).__name__}: {exc}"}
            log_text += traceback.format_exc()
        finally:
            _release_device(payload)
            lock.release()

        result_path = Path(payload["result_path"])
        if not cancelled and result_path.exists():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                result = {"error_code": "UNKNOWN", "message": "结果文件无法解析"}
        elif not cancelled and not result:
            result = {"error_code": "UNKNOWN", "message": f"未生成结果文件 (exit={exit_code})"}

        version = result.get("contract_version")
        if version is not None and version != collectors.CONTRACT_VERSION:
            log_text += (
                f"\n采集程序契约版本不符: 平台期望 {collectors.CONTRACT_VERSION}，"
                f"实际 {version}，请升级对应的采集程序"
            )

        duration_ms = int((time.time() - started) * 1000)
        _finalize(db, task, result, exit_code, log_text, duration_ms)
    finally:
        db.close()


class Worker(threading.Thread):
    def __init__(self, poll_interval: float = 3.0, agent_code: str = "") -> None:
        super().__init__(name="collector-worker", daemon=True)
        self.poll_interval = poll_interval
        self.agent_code = agent_code or settings.agent_code
        self._stop = threading.Event()
        self._last_heartbeat = 0.0

    def stop(self) -> None:
        self._stop.set()

    def _beat(self) -> None:
        now = time.time()
        if now - self._last_heartbeat < 60:
            return
        self._last_heartbeat = now
        try:
            with SessionLocal() as db:
                heartbeat(db, self.agent_code)
        except Exception:
            pass

    def run(self) -> None:
        self._beat()
        try:
            with SessionLocal() as db:
                leased = db.execute(
                    select(CollectionTask).where(
                        CollectionTask.status == "leased",
                        CollectionTask.agent_code == self.agent_code,
                    )
                ).scalars().all()
                for task in leased:
                    if task.cancel_requested:
                        task.status = "cancelled"
                        task.error_code = "CANCELLED"
                        task.error_msg = "用户停止任务"
                    else:
                        task.status = "queued"
                        task.lease_until = None
                        task.error_code = ""
                        task.error_msg = "执行进程重启，任务重新排队"
                if leased:
                    db.commit()
        except Exception:
            pass
        try:
            while not self._stop.is_set():
                claimed = None
                try:
                    self._beat()
                    with SessionLocal() as db:
                        claimed = claim_task(db, self.agent_code)
                except Exception:
                    claimed = None
                if claimed is None:
                    self._stop.wait(self.poll_interval)
                    continue
                try:
                    run_task(claimed.id)
                except Exception:
                    with SessionLocal() as db:
                        task = db.get(CollectionTask, claimed.id)
                        if task:
                            task.status = "failed"
                            task.error_code = "UNKNOWN"
                            task.error_msg = "worker 异常"
                            db.commit()
        finally:
            try:
                with SessionLocal() as db:
                    heartbeat(db, self.agent_code, status="offline")
            except Exception:
                pass
