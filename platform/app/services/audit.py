"""统一的审计写入。

只负责把记录挂到当前事务里，是否提交由调用方决定，保证业务动作和审计同生共死。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


def write_audit(
    db: Session,
    actor: str,
    action: str,
    target_type: str = "",
    target_id: Any = "",
    detail: dict | None = None,
    ip: str = "",
) -> None:
    db.add(
        AuditLog(
            actor=actor or "",
            action=action,
            target_type=target_type or "",
            target_id=str(target_id or ""),
            detail=detail or {},
            ip=ip or "",
        )
    )
