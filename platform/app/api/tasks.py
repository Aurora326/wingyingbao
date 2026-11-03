from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Artifact, AuditLog, BankAccount, CollectionTask, TaskRun, utcnow
from app.services import storage
from app.services.scheduler import enqueue_task, task_range, task_types_for

router = APIRouter(prefix="/api", tags=["tasks"])


@router.get("/tasks")
def list_tasks(
    status: str | None = None,
    account_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    conditions = []
    if status:
        conditions.append(CollectionTask.status == status)
    if account_id:
        conditions.append(CollectionTask.account_id == account_id)

    count_query = select(func.count(CollectionTask.id))
    query = select(CollectionTask)
    if conditions:
        count_query = count_query.where(*conditions)
        query = query.where(*conditions)
    total = db.execute(count_query).scalar() or 0
    tasks = db.execute(
        query.order_by(CollectionTask.id.desc())
        .offset(max(offset, 0))
        .limit(min(limit, 500))
    ).scalars().all()
    accounts = {a.id: a for a in db.execute(select(BankAccount)).scalars().all()}
    return {
        "total": total,
        "limit": min(limit, 500),
        "offset": max(offset, 0),
        "items": [
            {
                "id": t.id,
                "account": accounts[t.account_id].account_no if t.account_id in accounts else "",
                "bank_code": accounts[t.account_id].bank_code if t.account_id in accounts else "",
                "type": t.type,
                "start_date": t.start_date,
                "end_date": t.end_date,
                "status": t.status,
                "trigger": t.trigger,
                "agent_code": t.agent_code,
                "cancel_requested": bool(t.cancel_requested),
                "attempts": t.attempts,
                "error_code": t.error_code,
                "error_msg": t.error_msg,
                "created_at": t.created_at,
                "finished_at": t.finished_at,
            }
            for t in tasks
        ],
    }


@router.post("/tasks/{task_id}/stop")
def stop_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(CollectionTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status in ("succeeded", "failed", "cancelled", "stopped"):
        return {"ok": False, "status": task.status, "detail": "任务已结束"}

    now = utcnow()
    task.cancel_requested = True
    task.cancel_requested_at = now
    if task.status == "queued":
        task.status = "cancelled"
        task.error_code = "CANCELLED"
        task.error_msg = "用户停止任务"
        task.finished_at = now
        task.lease_until = None
        action = "cancelled"
    else:
        task.error_code = "STOPPING"
        task.error_msg = "正在停止任务"
        action = "stopping"

    db.add(
        AuditLog(
            actor="ui",
            action="task_stop",
            target_type="collection_task",
            target_id=str(task.id),
            detail={"status": task.status, "action": action},
            tenant_id=task.tenant_id,
        )
    )
    db.commit()
    return {
        "ok": True,
        "task_id": task.id,
        "status": task.status,
        "cancel_requested": True,
        "action": action,
    }


@router.post("/tasks/{task_id}/retry")
def retry_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(CollectionTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status in ("queued", "leased", "running"):
        return {"ok": False, "detail": "任务仍在执行中"}
    task.status = "queued"
    task.error_code = ""
    task.error_msg = ""
    task.lease_until = None
    task.finished_at = None
    task.cancel_requested = False
    task.cancel_requested_at = None
    db.commit()
    return {"ok": True}


@router.post("/accounts/{account_id}/enqueue")
def enqueue_manual(account_id: int, task_type: str = "statements", db: Session = Depends(get_db)):
    account = db.get(BankAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账户不存在")

    start, end = task_range(account)
    tasks = [
        enqueue_task(db, account, kind, start, end, trigger="manual", created_by="ui", force=True)
        for kind in task_types_for(task_type)
    ]
    return {"id": tasks[0].id}


ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def clean_log(text: str | None) -> str:
    """去掉终端颜色控制符，避免日志弹窗里出现 [32m / [0m 这类乱码。"""
    clean = ANSI_ESCAPE.sub("", text or "")
    clean = clean.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    return clean


@router.get("/tasks/{task_id}/log")
def task_log(task_id: int, db: Session = Depends(get_db)):
    runs = db.execute(
        select(TaskRun).where(TaskRun.task_id == task_id).order_by(TaskRun.id.desc()).limit(5)
    ).scalars().all()
    return {
        "items": [
            {
                "id": r.id,
                "attempt": r.attempt,
                "exit_code": r.exit_code,
                "duration_ms": r.duration_ms,
                "log": clean_log(r.log_text),
            }
            for r in runs
        ]
    }


@router.get("/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: int, db: Session = Depends(get_db)):
    artifact = db.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    path = storage.resolve(artifact.object_key)
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件已丢失")
    return FileResponse(path, filename=artifact.original_name or path.name)
