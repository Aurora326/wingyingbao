from __future__ import annotations

import base64
import datetime as dt
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import BankAccount, CollectionTask, Credential, UkeyDevice, utcnow
from app.schemas import AgentTaskResult
from app.services.executor import RETRY_LIMIT, _finalize
from app.services import security

LEASE_MINUTES = 30
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 60 * 1024 * 1024


class AgentTaskError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _release_stale(db: Session, agent_code: str, now: dt.datetime) -> None:
    stale = db.execute(
        select(CollectionTask).where(
            CollectionTask.status == "leased",
            CollectionTask.agent_code == agent_code,
            CollectionTask.lease_until.isnot(None),
            CollectionTask.lease_until < now,
        )
    ).scalars().all()
    for task in stale:
        if task.attempts < RETRY_LIMIT:
            task.status = "queued"
            task.error_code = "TIMEOUT"
            task.error_msg = "客户机执行超时，任务已重新排队"
        else:
            task.status = "failed"
            task.error_code = "TIMEOUT"
            task.error_msg = "客户机执行超时且超过重试次数"
        task.lease_until = None
    if stale:
        db.flush()


def _task_payload(db: Session, task: CollectionTask) -> dict:
    account = db.get(BankAccount, task.account_id)
    if account is None:
        raise AgentTaskError("任务关联的账户不存在", 409)
    device = db.get(UkeyDevice, account.ukey_device_id) if account.ukey_device_id else None
    extra = (device.extra or {}) if device else {}
    return {
        "task_id": task.id,
        "attempt": task.attempts,
        "agent_code": task.agent_code,
        "account_id": account.id,
        "bank_code": account.bank_code,
        "account_no": account.account_no,
        "account_name": account.account_name,
        "type": task.type,
        "start_date": task.start_date.isoformat(),
        "end_date": task.end_date.isoformat(),
        "options": account.driver_options or {},
        "device_id": device.id if device else 0,
        "device_backend": device.backend if device else "",
        "gf3_cabinet": device.gf3_cabinet if device else 0,
        "gf3_port": device.gf3_port if device else 0,
        "ter_id": str(extra.get("ter_id") or ""),
        "mb_user": str(extra.get("mb_user") or ""),
    }


def claim_agent_task(db: Session, agent_code: str) -> dict | None:
    now = utcnow()
    _release_stale(db, agent_code, now)
    task = db.execute(
        select(CollectionTask)
        .where(CollectionTask.status == "queued", CollectionTask.agent_code == agent_code)
        .order_by(CollectionTask.priority, CollectionTask.created_at)
        .limit(1)
    ).scalars().first()
    if task is None:
        db.commit()
        return None
    task.status = "leased"
    task.attempts += 1
    task.lease_until = now + dt.timedelta(minutes=LEASE_MINUTES)
    task.started_at = now
    task.error_code = ""
    task.error_msg = ""
    db.commit()
    return _task_payload(db, task)


def get_agent_task_credentials(
    db: Session, task_id: int, agent_code: str, attempt: int | None = None
) -> dict:
    task = db.get(CollectionTask, task_id)
    if task is None:
        raise AgentTaskError("任务不存在", 404)
    if task.agent_code != agent_code:
        raise AgentTaskError("任务不属于该 Agent", 403)
    if task.status not in ("leased", "running"):
        raise AgentTaskError(f"任务状态为 {task.status}，不能获取凭据", 409)
    if attempt is not None and task.attempts != attempt:
        raise AgentTaskError("任务执行轮次不一致，可能已被重新分配", 409)
    account = db.get(BankAccount, task.account_id)
    if account is None:
        raise AgentTaskError("任务关联的账户不存在", 409)
    credential = db.execute(
        select(Credential).where(Credential.bank_account_id == account.id)
    ).scalars().first()
    return {
        "task_id": task.id,
        "attempt": task.attempts,
        "username": security.decrypt(credential.username_enc) if credential else "",
        "password": security.decrypt(credential.password_enc) if credential else "",
        "pin": security.decrypt(credential.pin_enc) if credential else "",
    }


def _decode_files(task: CollectionTask, payload: AgentTaskResult) -> list[Path]:
    if not payload.files:
        return []
    work_dir = settings.tasks_dir / f"task_{task.id}_attempt_{payload.attempt}" / "uploads"
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    total = 0
    for item in payload.files:
        name = Path(item.name or "").name
        if not name or name != item.name:
            raise AgentTaskError(f"文件名不合法: {item.name}")
        try:
            content = base64.b64decode(item.content_b64, validate=True)
        except (ValueError, TypeError) as exc:
            raise AgentTaskError(f"文件内容不是合法 base64: {name}") from exc
        total += len(content)
        if len(content) > MAX_FILE_BYTES:
            raise AgentTaskError(f"单个文件超过 20MB: {name}")
        if total > MAX_TOTAL_BYTES:
            raise AgentTaskError("回传文件总大小超过 60MB")
        target = work_dir / name
        target.write_bytes(content)
        paths.append(target)
    return paths


def apply_agent_task_result(db: Session, task_id: int, payload: AgentTaskResult) -> dict:
    task = db.get(CollectionTask, task_id)
    if task is None:
        raise AgentTaskError("任务不存在", 404)
    if task.agent_code != payload.agent_code:
        raise AgentTaskError("任务不属于该 Agent", 403)
    if task.status not in ("leased", "running"):
        raise AgentTaskError(f"任务状态为 {task.status}，不能回传结果", 409)
    if task.attempts != payload.attempt:
        raise AgentTaskError("任务执行轮次不一致，可能已被重新分配", 409)

    files = _decode_files(task, payload)
    result = dict(payload.result or {})
    result["files"] = [str(path) for path in files]
    if payload.exit_code not in (0, None) and not result.get("error_code"):
        result["error_code"] = "UNKNOWN"
        result.setdefault("message", f"客户机采集进程退出码 {payload.exit_code}")

    try:
        _finalize(db, task, result, payload.exit_code, payload.log_text or "", payload.duration_ms)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise AgentTaskError(f"结果入库失败: {type(exc).__name__}: {exc}", 500) from exc
    return {
        "ok": True,
        "task_id": task.id,
        "status": task.status,
        "error_code": task.error_code,
        "error_msg": task.error_msg,
    }
