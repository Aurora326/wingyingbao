from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Artifact, BankAccount, CollectionTask, TaskRun
from app.services import storage
from app.services.scheduler import enqueue_task

router = APIRouter(prefix="/api", tags=["tasks"])


@router.get("/tasks")
def list_tasks(status: str | None = None, account_id: int | None = None, limit: int = 100, db: Session = Depends(get_db)):
    query = select(CollectionTask).order_by(CollectionTask.id.desc()).limit(min(limit, 500))
    if status:
        query = query.where(CollectionTask.status == status)
    if account_id:
        query = query.where(CollectionTask.account_id == account_id)
    tasks = db.execute(query).scalars().all()
    accounts = {a.id: a for a in db.execute(select(BankAccount)).scalars().all()}
    return {
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
                "attempts": t.attempts,
                "error_code": t.error_code,
                "error_msg": t.error_msg,
                "created_at": t.created_at,
                "finished_at": t.finished_at,
            }
            for t in tasks
        ]
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
    db.commit()
    return {"ok": True}


@router.post("/accounts/{account_id}/enqueue")
def enqueue_manual(account_id: int, task_type: str = "statements", db: Session = Depends(get_db)):
    account = db.get(BankAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账户不存在")
    from app.services.scheduler import task_range

    start, end = task_range(account)
    task = enqueue_task(db, account, task_type, start, end, trigger="manual", created_by="ui", force=True)
    return {"id": task.id}


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
                "log": r.log_text,
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
