"""统一的审计写入与字段级变更留痕。

只负责把记录挂到当前事务里，是否提交由调用方决定，保证业务动作和审计同生共死。
业务变更统一走 snapshot / write_changes / write_create / write_delete，
detail 契约：
    {"changes": {字段: {"from": 旧值, "to": 新值}}, "reason": "", "source": "manual"}
创建/删除使用 "snapshot"。
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog
from app.web.pagination import paginate_scalars

# 字段中文名注册表，同时充当“需要留痕的字段清单”。
# 凭据类字段不入此表，只记录“登录信息已更新”。
FIELD_LABELS: dict[str, dict[str, str]] = {
    "payment_doc": {
        "apply_no": "申请编号",
        "subject": "申请主题",
        "applicant": "申请人",
        "department": "申请部门",
        "apply_date": "申请日期",
        "approval_type": "付款审批类型",
        "payee_name": "收款单位",
        "payee_account_no": "收款账号",
        "payee_bank": "收款开户行",
        "payee_branch": "收款支行",
        "payer_name": "付款单位",
        "payer_account_no": "付款账号",
        "payer_bank": "付款开户行",
        "payer_sub_account_id": "付款子账簿",
        "amount": "申请金额",
        "paid_amount": "实付金额",
        "currency": "币种",
        "project_name": "项目信息",
        "contract_name": "合同名称",
        "contract_no": "合同编号",
        "approval_status": "审批状态",
        "payment_status": "支付状态",
        "approval_node": "当前节点",
        "approval_handler": "当前处理人",
        "status": "单据状态",
        "description": "说明",
    },
    "bank_account": {
        "account_name": "账户名称",
        "account_no": "账号",
        "bank_code": "银行",
        "status": "状态",
        "has_sub_accounts": "多级账簿",
    },
    "payee_account": {
        "payee_name": "收款单位",
        "account_no": "账号",
        "bank_name": "开户行",
        "branch_name": "支行",
        "status": "状态",
    },
    "sub_account": {
        "ledger_name": "账簿名称",
        "project_name": "对应项目",
        "status": "状态",
    },
}

SOURCE_LABELS = {
    "manual": "人工修改",
    "import": "导入覆盖",
    "sync": "系统同步",
    "agent": "设备上报",
    "legacy": "历史迁移",
}


def field_label(target_type: str, field: str) -> str:
    return FIELD_LABELS.get(target_type, {}).get(field, field)


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source or "")


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def snapshot(obj: Any, target_type: str, fields: list[str] | None = None) -> dict[str, str]:
    """按字段注册表取对象快照，值统一字符串化。"""
    keys = fields or list(FIELD_LABELS.get(target_type, {}).keys())
    return {key: _to_text(getattr(obj, key, None)) for key in keys if hasattr(obj, key)}


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


def write_changes(
    db: Session,
    obj: Any,
    before: dict[str, str],
    *,
    actor: str,
    action: str,
    target_type: str,
    reason: str = "",
    source: str = "manual",
    ip: str = "",
    label: str = "",
    extra: dict | None = None,
) -> bool:
    """比较快照差异并写入一条字段级变更记录；无变化不写。"""
    after = snapshot(obj, target_type)
    changes = {
        key: {"from": before.get(key, ""), "to": after[key]}
        for key in after
        if before.get(key, "") != after[key]
    }
    if not changes:
        return False
    detail: dict[str, Any] = {"changes": changes, "reason": reason or "", "source": source}
    if label:
        detail["label"] = label
    if extra:
        detail.update(extra)
    write_audit(db, actor, action, target_type, getattr(obj, "id", ""), detail, ip)
    return True


def write_create(
    db: Session,
    obj: Any,
    *,
    actor: str,
    action: str,
    target_type: str,
    source: str = "manual",
    ip: str = "",
    label: str = "",
) -> None:
    detail: dict[str, Any] = {"snapshot": snapshot(obj, target_type), "source": source}
    if label:
        detail["label"] = label
    write_audit(db, actor, action, target_type, getattr(obj, "id", ""), detail, ip)


def write_delete(
    db: Session,
    obj: Any,
    *,
    actor: str,
    action: str,
    target_type: str,
    source: str = "manual",
    ip: str = "",
    label: str = "",
) -> None:
    detail: dict[str, Any] = {"snapshot": snapshot(obj, target_type), "source": source}
    if label:
        detail["label"] = label
    write_audit(db, actor, action, target_type, getattr(obj, "id", ""), detail, ip)


def list_changes(
    db: Session,
    target_type: str,
    target_id: Any,
    page: int = 1,
    size: int = 10,
) -> dict[str, Any]:
    """按对象查询更新记录，走 (tenant_id, target_type, target_id, id) 复合索引。"""
    query = (
        select(AuditLog)
        .where(AuditLog.target_type == target_type, AuditLog.target_id == str(target_id))
        .order_by(AuditLog.id.desc())
    )
    return paginate_scalars(db, query, page, size)
