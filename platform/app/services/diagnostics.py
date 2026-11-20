"""失败现场诊断附件存储、查询与自动过期清理。"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import TaskDiagnostic

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".html", ".htm", ".json", ".txt", ".zip"}
MAX_DIAG_FILE_BYTES = 15 * 1024 * 1024
VALID_TASK_KINDS = {"collection", "draft"}
VALID_DIAG_KINDS = {"screenshot", "dom", "meta", "trace", "log"}

ERROR_CODE_LABELS: dict[str, str] = {
    "PAGE_CHANGED": "页面结构变化（疑似网银改版）",
    "AUTH_FAILED": "登录认证失败（密码/验证码/证书）",
    "UKEY_ERROR": "UKey 或机柜端口异常",
    "BANK_TEMP_ERROR": "网银临时故障/网络超时",
    "PLUGIN_CRASH": "脚本运行异常",
    "COLLECT_FAILED": "采集执行失败",
    "DRAFT_FAILED": "制单执行失败",
    "LEASE_EXPIRED": "执行超时待人工核实",
    "TIMEOUT": "执行超时",
}


class DiagnosticError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def infer_kind(filename: str, explicit: str = "") -> str:
    if explicit in VALID_DIAG_KINDS:
        return explicit
    suffix = Path(filename).suffix.lower()
    if suffix in (".jpg", ".jpeg", ".png", ".webp"):
        return "screenshot"
    if suffix in (".html", ".htm"):
        return "dom"
    if suffix == ".json":
        return "meta"
    if suffix == ".zip":
        return "trace"
    return "log"


def _safe_filename(raw: str) -> str:
    base = Path(raw or "").name
    base = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]", "_", base)
    if not base or base in (".", ".."):
        raise DiagnosticError("诊断文件名不合法")
    suffix = Path(base).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise DiagnosticError(f"不支持的诊断文件类型: {suffix or '无扩展名'}")
    return base[:180]


def save_diagnostic_file(
    db: Session,
    *,
    task_kind: str,
    task_id: int,
    attempt: int,
    filename: str,
    content: bytes,
    content_type: str = "",
    kind: str = "",
    error_code: str = "",
    stage: str = "",
    tenant_id: int | None = None,
) -> TaskDiagnostic:
    if task_kind not in VALID_TASK_KINDS:
        raise DiagnosticError("无效的任务类型")
    if len(content) > MAX_DIAG_FILE_BYTES:
        raise DiagnosticError("单个诊断文件不能超过 15MB", 413)
    safe_name = _safe_filename(filename)
    resolved_kind = infer_kind(safe_name, kind)

    rel_dir = Path(f"{task_kind}_{task_id}_a{max(1, int(attempt))}")
    abs_dir = settings.diagnostics_dir / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)
    target = abs_dir / safe_name
    if target.exists():
        stem = target.stem
        suffix = target.suffix
        stamp = dt.datetime.now().strftime("%H%M%S%f")
        target = abs_dir / f"{stem}_{stamp}{suffix}"
    target.write_bytes(content)

    object_key = (rel_dir / target.name).as_posix()
    row = TaskDiagnostic(
        task_kind=task_kind,
        task_id=task_id,
        attempt=max(1, int(attempt)),
        kind=resolved_kind,
        error_code=(error_code or "")[:32],
        stage=(stage or "")[:64],
        original_name=safe_name,
        object_key=object_key,
        content_type=(content_type or "")[:64],
        size=len(content),
        tenant_id=tenant_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def resolve_diagnostic_path(row: TaskDiagnostic) -> Path:
    root = settings.diagnostics_dir.resolve()
    target = (root / row.object_key).resolve()
    if root not in target.parents and target != root:
        raise DiagnosticError("非法的诊断文件路径", 403)
    return target


def list_diagnostics(db: Session, task_kind: str, task_id: int) -> list[TaskDiagnostic]:
    return (
        db.execute(
            select(TaskDiagnostic)
            .where(TaskDiagnostic.task_kind == task_kind, TaskDiagnostic.task_id == task_id)
            .order_by(TaskDiagnostic.id.desc())
        )
        .scalars()
        .all()
    )


def count_diagnostics_by_tasks(db: Session, task_kind: str, task_ids: list[int]) -> dict[int, int]:
    if not task_ids:
        return {}
    rows = db.execute(
        select(TaskDiagnostic.task_id, func.count(TaskDiagnostic.id))
        .where(TaskDiagnostic.task_kind == task_kind, TaskDiagnostic.task_id.in_(task_ids))
        .group_by(TaskDiagnostic.task_id)
    ).all()
    return {int(task_id): int(cnt) for task_id, cnt in rows}


def cleanup_expired_diagnostics(
    db: Session,
    keep_days: int | None = None,
    now: dt.datetime | None = None,
) -> int:
    """清理超过保留期的失败现场诊断记录与本地磁盘文件。"""
    days = settings.diagnostic_keep_days if keep_days is None else int(keep_days)
    if days <= 0:
        return 0
    cutoff = (now or dt.datetime.now()) - dt.timedelta(days=days)
    expired = (
        db.execute(select(TaskDiagnostic).where(TaskDiagnostic.created_at < cutoff))
        .scalars()
        .all()
    )
    if not expired:
        return 0
    dirs_to_check: set[Path] = set()
    for row in expired:
        try:
            path = resolve_diagnostic_path(row)
            if path.is_file():
                path.unlink(missing_ok=True)
            dirs_to_check.add(path.parent)
        except Exception:
            pass
        db.delete(row)
    db.commit()
    for directory in dirs_to_check:
        try:
            if directory.exists() and not any(directory.iterdir()):
                directory.rmdir()
        except OSError:
            pass
    return len(expired)
