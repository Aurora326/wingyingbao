from __future__ import annotations

from decimal import Decimal

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Artifact,
    AuditLog,
    BalanceSnapshot,
    BankAccount,
    CollectionTask,
    Credential,
    Receipt,
    ReconBatch,
    ReconItem,
    Statement,
    TaskRun,
)
from app.services import storage

ACTIVE_STATUSES = ("queued", "leased", "running")


class AccountAdminError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _count(db: Session, model, *conditions) -> int:
    return db.execute(select(func.count()).select_from(model).where(*conditions)).scalar_one()


def account_stats(db: Session, account_id: int) -> dict:
    run_ids = select(TaskRun.id).join(CollectionTask, TaskRun.task_id == CollectionTask.id).where(
        CollectionTask.account_id == account_id
    )
    statements = _count(db, Statement, Statement.account_id == account_id)
    receipts = _count(db, Receipt, Receipt.account_id == account_id)
    artifacts = _count(
        db,
        Artifact,
        or_(Artifact.account_id == account_id, Artifact.task_run_id.in_(run_ids)),
    )
    tasks = _count(db, CollectionTask, CollectionTask.account_id == account_id)
    active_tasks = _count(
        db,
        CollectionTask,
        CollectionTask.account_id == account_id,
        CollectionTask.status.in_(ACTIVE_STATUSES),
    )
    return {
        "statements": statements,
        "receipts": receipts,
        "artifacts": artifacts,
        "tasks": tasks,
        "active_tasks": active_tasks,
        "has_data": bool(statements or receipts or artifacts),
        "can_delete": active_tasks == 0,
    }


def toggle_collection(db: Session, account_id: int) -> bool:
    account = db.get(BankAccount, account_id)
    if account is None:
        raise AccountAdminError("账户不存在", status_code=404)
    account.collect_enabled = not account.collect_enabled
    db.commit()
    return account.collect_enabled


def delete_account(db: Session, account_id: int, confirm: str) -> dict:
    account = db.get(BankAccount, account_id)
    if account is None:
        raise AccountAdminError("账户不存在", status_code=404)
    stats = account_stats(db, account_id)
    if stats["active_tasks"]:
        raise AccountAdminError("该账户有正在执行的采集任务，请等待任务结束后再删除", status_code=409)
    if (confirm or "").strip() != account.account_no:
        raise AccountAdminError("请输入完整账号以确认删除", status_code=400)

    statement_ids = list(
        db.execute(select(Statement.id).where(Statement.account_id == account_id)).scalars().all()
    )
    task_ids = list(
        db.execute(select(CollectionTask.id).where(CollectionTask.account_id == account_id)).scalars().all()
    )
    run_ids = list(
        db.execute(select(TaskRun.id).where(TaskRun.task_id.in_(task_ids))).scalars().all()
    ) if task_ids else []
    artifacts = db.execute(
        select(Artifact).where(
            or_(Artifact.account_id == account_id, Artifact.task_run_id.in_(run_ids))
            if run_ids
            else Artifact.account_id == account_id
        )
    ).scalars().all()
    artifact_ids = [artifact.id for artifact in artifacts]
    artifact_keys = [artifact.object_key for artifact in artifacts]
    batch_ids = list(
        db.execute(
            select(ReconItem.batch_id).where(ReconItem.statement_id.in_(statement_ids)).distinct()
        ).scalars().all()
    ) if statement_ids else []

    if statement_ids:
        db.execute(delete(ReconItem).where(ReconItem.statement_id.in_(statement_ids)))
    db.execute(delete(Statement).where(Statement.account_id == account_id))
    db.execute(delete(Receipt).where(Receipt.account_id == account_id))
    if artifact_ids:
        db.execute(delete(Artifact).where(Artifact.id.in_(artifact_ids)))
    if run_ids:
        db.execute(delete(TaskRun).where(TaskRun.id.in_(run_ids)))
    if task_ids:
        db.execute(delete(CollectionTask).where(CollectionTask.id.in_(task_ids)))
    db.execute(delete(BalanceSnapshot).where(BalanceSnapshot.account_id == account_id))
    db.execute(delete(Credential).where(Credential.bank_account_id == account_id))

    for batch_id in batch_ids:
        batch = db.get(ReconBatch, batch_id)
        if batch is None:
            continue
        remaining = db.execute(select(ReconItem).where(ReconItem.batch_id == batch_id)).scalars().all()
        if not remaining:
            db.delete(batch)
            continue
        batch.matched_count = sum(1 for item in remaining if item.status == "matched")
        batch.unmatched_count = sum(1 for item in remaining if item.status == "unmatched")
        batch.diff_amount = sum(
            (item.diff_amount or Decimal("0") for item in remaining if item.status != "matched"),
            Decimal("0"),
        )

    db.add(
        AuditLog(
            actor="ui",
            action="account_delete",
            target_type="bank_account",
            target_id=str(account_id),
            detail={
                "account_no": account.account_no,
                "statements": stats["statements"],
                "receipts": stats["receipts"],
                "artifacts": stats["artifacts"],
                "tasks": stats["tasks"],
            },
            tenant_id=account.tenant_id,
        )
    )
    db.delete(account)
    db.commit()

    for key in artifact_keys:
        remaining = db.execute(select(func.count()).select_from(Artifact).where(Artifact.object_key == key)).scalar_one()
        if not remaining:
            try:
                storage.delete(key)
            except OSError:
                pass

    return {
        "ok": True,
        "deleted": stats,
        "artifacts_removed": len(artifact_keys),
    }
