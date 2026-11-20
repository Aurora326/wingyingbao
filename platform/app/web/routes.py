from __future__ import annotations

import datetime as dt
import threading
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api import accounts as accounts_api
from app.api import dashboard as dashboard_api
from app.api import data as data_api
from app.api import recon as recon_api
from app.api import tasks as tasks_api
from app.config import settings
from app.db import SessionLocal
from app.models import Agent, AlertEvent, AppUser, Artifact, AuditLog, BankAccount, CollectionTask, LoginAttempt, PaymentCollectTask, Receipt, Statement, TaskRun, Tenant
from app.services import alerts, app_settings, login_guard, security
from app.services import permissions
from app.services.audit import field_label, list_changes, source_label, write_audit
from app.services.backup import run_backup_job
from app.services.diagnostics import count_diagnostics_by_tasks
from app.services.abc_ledger import sub_region
from app.services.banks import bank_label
from app.services.banks import list_banks
from app.services.payment_collect import (
    get_source_credentials,
    get_source_schedule,
    task_dict,
)

from app.web.pagination import PAGE_SIZES, is_hx, normalize_size, page_url, paginate_items, paginate_scalars

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
templates.env.globals["field_label"] = field_label
templates.env.globals["source_label"] = source_label
_STATIC_DIR = Path(__file__).resolve().parent / "static"


def _asset_version() -> str:
    files = [_STATIC_DIR / name for name in ("app.css", "app.js", "ui.js", "htmx.min.js")]
    try:
        return str(int(max(path.stat().st_mtime for path in files)))
    except OSError:
        return "1"


templates.env.globals["asset_version"] = _asset_version
templates.env.globals["can_menu"] = permissions.can_menu
templates.env.globals["can_edit_accounts"] = permissions.can_edit_accounts
templates.env.globals["can_collect_accounts"] = permissions.can_collect_accounts
templates.env.globals["role_label"] = permissions.role_label
templates.env.globals["bank_label"] = bank_label
templates.env.globals["page_url"] = page_url
templates.env.globals["PAGE_SIZES"] = PAGE_SIZES


def _fmt_money(value) -> str:
    if value in (None, ""):
        return "—"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_date(value) -> str:
    if not value:
        return "—"
    if isinstance(value, str):
        return value
    return value.strftime("%Y-%m-%d")


templates.env.filters["money"] = _fmt_money
templates.env.filters["dt"] = _fmt_date

_AUDIT_ACTORS = {"worker": "采集服务", "system": "系统"}
_AUDIT_ACTIONS = {
    "task_finished": "采集任务结束",
    "task_stop": "停止采集任务",
    "usb_robot_sync": "同步 UKey 设备",
    "agent_device_report": "客户侧 Agent 上报设备",
    "account_create": "创建银行账户",
    "account_update": "更新银行账户",
    "account_device_update": "调整账户 UKey",
    "credential_update": "更新登录信息",
    "user_create": "新建用户",
    "user_edit": "编辑用户",
    "cashier_scope": "分配出纳账套",
    "cashier_delegation": "出纳委托",
    "cashier_delegation_self": "出纳自助委托",
    "user_password_self": "修改自己的密码",
    "user_role": "调整用户角色",
    "user_toggle": "启用或停用用户",
    "user_password": "重置用户密码",
    "login_locked": "登录失败锁定",
    "payment_import": "导入付款单据",
    "payment_confirm": "确认付款账户",
    "payment_edit": "编辑付款单据",
    "payment_review": "审核付款单据",
    "payment_export": "导出制单草稿",
    "draft_batch_create": "生成制单批次",
    "draft_task_done": "标记制单完成",
    "draft_task_cancel": "撤回制单任务",
    "draft_task_timeout": "制单超时转人工核实",
    "draft_task_requeue": "人工核实后重发制单",
    "plugin_upload": "上传采集插件包",
    "plugin_activate": "切换插件生效版本",
    "plugin_deactivate": "停用插件版本",
    "agent_channel_update": "调整执行机发布通道",
    "payee_create": "新建收款账户",
    "payee_change": "变更收款账户",
}
_AUDIT_TARGETS = {
    "collection_task": "采集任务",
    "ukey_device": "UKey 设备",
    "bank_account": "银行账户",
    "agent": "采集 Agent",
    "app_user": "平台用户",
    "payment_doc": "付款单据",
    "payment_draft": "制单草稿",
    "payment_draft_batch": "制单批次",
    "payment_draft_task": "制单任务",
    "payee_account": "收款账户",
    "plugin_release": "采集插件",
}
_AUDIT_STATUS = {"succeeded": "已完成", "failed": "未完成", "running": "进行中", "cancelled": "已停止"}

# 人操作平台产生的动作；其余（Agent 上报、任务结束）属于机器事件，不进审计主视图
_AUDIT_USER_ACTIONS = {
    "account_create",
    "account_update",
    "account_device_update",
    "credential_update",
    "user_create",
    "user_edit",
    "user_role",
    "user_toggle",
    "user_password",
    "user_password_self",
    "login_locked",
    "cashier_scope",
    "cashier_delegation",
    "cashier_delegation_self",
    "task_stop",
    "task_stop_batch",
    "payment_import",
    "payment_confirm",
    "payment_edit",
    "payment_review",
    "payment_export",
    "draft_batch_create",
    "draft_task_done",
    "draft_task_cancel",
    "payee_create",
    "payee_change",
}
_AUDIT_ERRORS = {
    "LOGIN_FAILED": "登录失败",
    "SELECTOR_NOT_FOUND": "页面元素未找到",
    "AUTH_FAILED": "认证失败",
    "UKEY_NOT_FOUND": "未检测到 UKey",
    "TIMEOUT": "操作超时",
    "NETWORK_ERROR": "网络异常",
    "COLLECT_FAILED": "采集失败",
    "CANCELLED": "用户停止",
    "STOPPING": "停止中",
    "UNKNOWN": "未知错误",
}


def _audit_actor(value: str) -> str:
    return _AUDIT_ACTORS.get(value, value or "—")


def _audit_action(value: str) -> str:
    return _AUDIT_ACTIONS.get(value, value or "—")


def _audit_target(log: AuditLog) -> str:
    label = _AUDIT_TARGETS.get(log.target_type, log.target_type or "—")
    return f"{label} #{log.target_id}" if log.target_id and log.target_id != "all" else label


def _audit_summary(log: AuditLog) -> str:
    detail = log.detail or {}
    if log.action == "task_finished":
        status = _AUDIT_STATUS.get(detail.get("status"), detail.get("status") or "结束")
        if detail.get("status") == "failed":
            error = _AUDIT_ERRORS.get(detail.get("error_code"), detail.get("error_code") or "")
            return f"采集任务{status}" + (f"（{error}）" if error else "")
        ingest = detail.get("ingest") or {}
        inserted = ingest.get("inserted", 0)
        return f"采集任务{status}，新增 {inserted} 条数据" if inserted else f"采集任务{status}"
    if log.action == "task_stop":
        return "用户停止采集任务" if detail.get("action") == "cancelled" else "正在停止采集任务"
    if log.action == "user_create":
        return f"新建用户 {detail.get('username', '')}"
    if log.action == "user_role":
        return f"将用户 {detail.get('username', '')} 的角色改为 {detail.get('role', '')}"
    if log.action == "user_toggle":
        return f"{'启用' if detail.get('is_active') else '停用'}用户 {detail.get('username', '')}"
    if log.action == "user_password":
        return f"重置用户 {detail.get('username', '')} 的密码"
    if log.action == "login_locked":
        return (
            f"账户 {log.actor or detail.get('username', '')} 连续登录失败 "
            f"{detail.get('failures', 0)} 次，已临时锁定"
        )
    if log.action == "payment_import":
        return (
            f"导入付款单据：新增 {detail.get('inserted', 0)}，"
            f"更新 {detail.get('updated', 0)}，跳过 {detail.get('skipped', 0)}"
        )
    if log.action == "payment_confirm":
        return f"确认单据 {detail.get('apply_no', '')}，金额 {detail.get('amount', '')}"
    if log.action == "payment_edit":
        return f"编辑单据 {detail.get('apply_no', '')}"
    if log.action == "payment_review":
        decision = "通过" if detail.get("decision") == "approve" else "驳回"
        return f"{decision}单据 {detail.get('apply_no', '')}，金额 {detail.get('amount', '')}"
    if log.action == "payment_export":
        return f"导出 {detail.get('count', 0)} 张单据，合计 {detail.get('amount', '')}"
    if log.action == "draft_batch_create":
        return (
            f"生成批次 {detail.get('batch_no', '')}，"
            f"{detail.get('doc_count', 0)} 张单据，合计 {detail.get('amount', '')}"
        )
    if log.action == "draft_task_done":
        return (
            f"制单完成：{detail.get('bank_code', '')} "
            f"{detail.get('doc_count', 0)} 张，合计 {detail.get('amount', '')}"
        )
    if log.action == "draft_task_cancel":
        return f"撤回制单任务：{detail.get('bank_code', '')} {detail.get('doc_count', 0)} 张"
    if log.action == "payee_create":
        return f"新建收款账户 {detail.get('payee_name', '')}（{detail.get('account_no', '')}）"
    if log.action == "payee_change":
        fields = "、".join((detail.get("changes") or {}).keys())
        return f"变更收款账户：{fields}" if fields else "变更收款账户"
    if log.action == "usb_robot_sync":
        source = "本地 MB" if detail.get("source") == "local" else "服务商接口"
        suffix = f"，{detail.get('created', 0)} 新增 / {detail.get('updated', 0)} 更新" if detail.get("created") is not None else ""
        return (
            f"从{source}同步设备 {detail.get('total', 0)} 台{suffix}"
        )
    if log.action == "agent_device_report":
        return (
            f"客户侧 Agent 上报机柜 {detail.get('ter_id', '')}："
            f"{detail.get('total', 0)} 台设备，新增 {detail.get('created', 0)}，"
            f"更新 {detail.get('updated', 0)}"
        )
    return ""


def _audit_details(log: AuditLog) -> list[tuple[str, str]]:
    detail = log.detail or {}
    rows: list[tuple[str, str]] = []
    if detail.get("status"):
        rows.append(("结果", _AUDIT_STATUS.get(detail["status"], detail["status"])))
    if detail.get("error_code"):
        code = detail["error_code"]
        rows.append(("错误", f"{_AUDIT_ERRORS.get(code, code)}（{code}）"))
    ingest = detail.get("ingest")
    if isinstance(ingest, dict):
        rows.append(
            (
                "入库",
                f"新增 {ingest.get('inserted', 0)} · 重复 {ingest.get('duplicates', 0)} · 失败 {ingest.get('failed', 0)}",
            )
        )
    if detail.get("errors"):
        rows.append(("异常", "；".join(str(item) for item in detail["errors"][:3])))
    if detail.get("source"):
        rows.append(("来源", "本地 MB" if detail["source"] == "local" else "服务商接口"))
    if detail.get("terminals"):
        rows.append(("机柜", "、".join(str(item) for item in detail["terminals"][:5])))
    if detail.get("artifacts") is not None:
        artifacts = detail.get("artifacts")
        count = len(artifacts) if isinstance(artifacts, (list, tuple)) else artifacts
        rows.append(("附件", f"{count} 个文件"))
    for key in ("total", "created", "updated"):
        if key in detail:
            label = {"total": "设备总数", "created": "新增", "updated": "更新"}[key]
            rows.append((label, str(detail[key])))
    for key, label in (
        ("apply_no", "单据"),
        ("amount", "金额"),
        ("from_status", "原状态"),
        ("to_status", "新状态"),
        ("opinion", "审核意见"),
        ("batch_no", "批次号"),
        ("count", "数量"),
        ("payee_name", "收款单位"),
        ("account_no", "账号"),
        ("reason", "原因"),
        ("file", "文件"),
    ):
        if detail.get(key) not in (None, ""):
            rows.append((label, str(detail[key])))
    if detail.get("decision"):
        rows.append(("审核结论", "通过" if detail["decision"] == "approve" else "驳回"))
    changes = detail.get("changes")
    if isinstance(changes, dict):
        parts = []
        for key, value in changes.items():
            if isinstance(value, dict):
                parts.append(f"{key}：{value.get('from', '')} 改为 {value.get('to', '')}")
            else:
                parts.append(f"{key}：{value}")
        rows.append(("变更", "；".join(parts)))
    apply_nos = detail.get("apply_nos")
    if isinstance(apply_nos, list) and apply_nos:
        rows.append(("单据号", "、".join(str(item) for item in apply_nos[:10])))
    return rows


templates.env.filters["audit_actor"] = _audit_actor
templates.env.filters["audit_action"] = _audit_action
templates.env.filters["audit_target"] = _audit_target
templates.env.filters["audit_summary"] = _audit_summary
templates.env.filters["audit_details"] = _audit_details


@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={"error": ""})


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    username = username.strip()
    ip = request.client.host if request.client else ""
    with SessionLocal() as db:
        remaining = login_guard.lock_remaining(db, username)
        if remaining > 0:
            minutes = max(1, (remaining + 59) // 60)
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={"error": f"尝试次数过多，请 {minutes} 分钟后再试"},
                status_code=429,
            )
        user = db.execute(select(AppUser).where(AppUser.username == username)).scalars().first()
        if (
            user is None
            or not user.is_active
            or not security.verify_password(password, user.password_hash)
        ):
            count, locked = login_guard.record_failure(db, username, ip)
            if locked:
                write_audit(
                    db,
                    username,
                    "login_locked",
                    "app_user",
                    username,
                    {"ip": ip, "failures": count},
                )
                db.commit()
                return templates.TemplateResponse(
                    request=request,
                    name="login.html",
                    context={"error": f"尝试次数过多，请 {login_guard.LOCK_MINUTES} 分钟后再试"},
                    status_code=429,
                )
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={"error": "用户名或密码错误"},
                status_code=401,
            )
        login_guard.clear_failures(db, username)
        needs_change = security.is_default_password(user.username, user.password_hash)
    response = RedirectResponse("/profile?force=1" if needs_change else "/", status_code=303)
    response.set_cookie("session", security.create_session(username), httponly=True, samesite="lax")
    if needs_change:
        response.set_cookie(
            "pwd_change",
            "1",
            max_age=security.SESSION_TTL,
            httponly=True,
            samesite="lax",
        )
    return response


@router.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("session")
    response.delete_cookie("tenant_id")
    return response


@router.post("/tenant/switch")
def tenant_switch(request: Request, tenant_id: str = Form("")):
    user = getattr(request.state, "user", None)
    if user is None or user.tenant_id is not None:
        raise HTTPException(status_code=403, detail="只有平台管理员可以切换租户")
    response = RedirectResponse("/settings", status_code=303)
    if tenant_id:
        with SessionLocal() as db:
            tenant = db.get(Tenant, int(tenant_id)) if tenant_id.isdigit() else None
        if tenant is None or tenant.status != "active":
            raise HTTPException(status_code=400, detail="租户不存在或已停用")
        response.set_cookie("tenant_id", tenant_id, httponly=True, samesite="lax")
    else:
        response.delete_cookie("tenant_id")
    return response


@router.get("/")
def dashboard_page(request: Request):
    with SessionLocal() as db:
        summary = dashboard_api.summary(db)
        recent_tasks = tasks_api.list_tasks(limit=8, db=db)["items"]
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"page": "dashboard", "s": summary, "tasks": recent_tasks},
    )


TASK_PAGE_SIZE = 20


def _default_task_date(value: str | None) -> str:
    """未带参数时默认只看当天，显式传 all 表示不限日期。"""
    if value is None:
        return dt.date.today().isoformat()
    if value == "all":
        return ""
    return value


def _task_list_context(
    db,
    status: str,
    page: int = 1,
    size: int = TASK_PAGE_SIZE,
    date_from: str = "",
    date_to: str = "",
) -> dict:
    page = max(page, 1)
    date_args = {"date_from": date_from, "date_to": date_to}
    result = tasks_api.list_tasks(
        status=status or None,
        limit=size,
        offset=(page - 1) * size,
        db=db,
        **date_args,
    )
    total = result["total"]
    pages = max(1, (total + size - 1) // size)
    if page > pages:
        page = pages
        result = tasks_api.list_tasks(
            status=status or None,
            limit=size,
            offset=(page - 1) * size,
            db=db,
            **date_args,
        )
    count_query = select(CollectionTask.status, func.count(CollectionTask.id))
    date_filters = tasks_api.task_date_filters(date_from, date_to)
    if date_filters:
        count_query = count_query.where(*date_filters)
    status_counts = dict(
        db.execute(count_query.group_by(CollectionTask.status)).all()
    )
    last_task = db.execute(
        select(CollectionTask).order_by(CollectionTask.id.desc()).limit(1)
    ).scalars().first()
    stats = {
        "total": sum(status_counts.values()),
        "running": sum(status_counts.get(key, 0) for key in ("queued", "leased", "running")),
        "succeeded": status_counts.get("succeeded", 0),
        "failed": status_counts.get("failed", 0),
        "cancelled": status_counts.get("cancelled", 0),
        "last": last_task,
    }

    # 展示用“当日第 N 次”，不动数据库主键
    items = result["items"]
    days = {t["created_at"].date() for t in items if t.get("created_at")}
    if days:
        rows = db.execute(
            select(CollectionTask.id, CollectionTask.created_at)
            .where(func.date(CollectionTask.created_at).in_(list(days)))
            .order_by(CollectionTask.created_at.asc(), CollectionTask.id.asc())
        ).all()
        counters: dict = {}
        daily_seq: dict = {}
        for task_id, created_at in rows:
            day = created_at.date()
            counters[day] = counters.get(day, 0) + 1
            daily_seq[task_id] = counters[day]
        for task in items:
            task["daily_seq"] = daily_seq.get(task["id"])

    # 每个任务实际入库的笔数（流水/回单 → 产物 → 任务运行）
    stmt_counts: dict = {}
    rcpt_counts: dict = {}
    task_ids = [task["id"] for task in items]
    if task_ids:
        stmt_counts = dict(
            db.execute(
                select(TaskRun.task_id, func.count(Statement.id))
                .join(Artifact, Artifact.task_run_id == TaskRun.id)
                .join(Statement, Statement.artifact_id == Artifact.id)
                .where(TaskRun.task_id.in_(task_ids))
                .group_by(TaskRun.task_id)
            ).all()
        )
        rcpt_counts = dict(
            db.execute(
                select(TaskRun.task_id, func.count(Receipt.id))
                .join(Artifact, Artifact.task_run_id == TaskRun.id)
                .join(Receipt, Receipt.artifact_id == Artifact.id)
                .where(TaskRun.task_id.in_(task_ids), Receipt.receipt_no != "")
                .group_by(TaskRun.task_id)
            ).all()
        )
    diag_counts = count_diagnostics_by_tasks(db, "collection", task_ids) if task_ids else {}
    for task in items:
        if task.get("type") == "receipts":
            task["ingested"] = rcpt_counts.get(task["id"], 0)
        else:
            task["ingested"] = stmt_counts.get(task["id"], 0)
        task["diag_count"] = diag_counts.get(task["id"], 0)

    return {
        "tasks": result["items"],
        "status": status,
        "stats": stats,
        "page_no": page,
        "pages": pages,
        "total": total,
        "size": size,
        "date_from": date_from,
        "date_to": date_to,
        "today": dt.date.today().isoformat(),
        "counts": status_counts,
    }


def _payment_task_list_context(db, status: str, page: int = 1, size: int = 20, source: str = "payment") -> dict:
    page = max(page, 1)
    source_key = "oa" if source == "oa" else "mingyuan"
    query = select(PaymentCollectTask).where(PaymentCollectTask.source == source_key)
    if status:
        query = query.where(PaymentCollectTask.status == status)
    total = db.execute(query.with_only_columns(func.count(PaymentCollectTask.id)).order_by(None)).scalar_one()
    pages = max(1, (total + size - 1) // size)
    if page > pages:
        page = pages
    rows = db.execute(
        query.order_by(PaymentCollectTask.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).scalars().all()
    counts = dict(
        db.execute(
            select(PaymentCollectTask.status, func.count(PaymentCollectTask.id))
            .where(PaymentCollectTask.source == source_key)
            .group_by(PaymentCollectTask.status)
        ).all()
    )
    stats = {
        "total": sum(counts.values()),
        "running": sum(counts.get(key, 0) for key in ("queued", "running")),
        "succeeded": counts.get("succeeded", 0),
        "failed": counts.get("failed", 0),
        "cancelled": 0,
    }
    return {
        "payment_tasks": [task_dict(item) for item in rows],
        "counts": counts,
        "status": status,
        "stats": stats,
        "page_no": page,
        "pages": pages,
        "total": total,
        "size": size,
        "task_source": "oa" if source == "oa" else "payment",
        "source": "oa" if source == "oa" else "payment",
    }


def _payment_config_context(db, is_admin: bool = False) -> dict:
    my_user, my_pwd = get_source_credentials(db)
    oa_user, oa_pwd = get_source_credentials(db, "oa")
    return {
        "source_username": my_user,
        "source_configured": bool(my_user and my_pwd),
        "oa_username": oa_user,
        "oa_configured": bool(oa_user and oa_pwd),
        "schedule": get_source_schedule(db),
        "oa_schedule": get_source_schedule(db, "oa"),
        "is_admin": is_admin,
    }


@router.get("/tasks")
def tasks_page(
    request: Request,
    status: str = "",
    page: int = 1,
    size: int = TASK_PAGE_SIZE,
    source: str = "",
    date_from: str | None = None,
    date_to: str = "",
):
    denied = _deny_menu(request, "tasks")
    if denied:
        return denied
    date_from = _default_task_date(date_from)
    resolved_size = normalize_size(size, TASK_PAGE_SIZE)
    with SessionLocal() as db:
        if source in ("payment", "oa"):
            context = _payment_task_list_context(db, status, page, resolved_size, source=source)
            if not is_hx(request, "task-region"):
                user = getattr(request.state, "user", None)
                context.update(
                    _payment_config_context(
                        db,
                        bool(user and permissions.can_menu(user.role, "payment_settings")),
                    )
                )
        else:
            context = _task_list_context(db, status, page, resolved_size, date_from, date_to)
            if not is_hx(request, "task-region"):
                context["accounts"] = accounts_api.list_accounts(db)["items"]
            context["task_source"] = "bank"
            context["source"] = ""
    context.update(page="tasks")
    context["msg"] = request.query_params.get("msg", "")
    tpl = "_tasks_region.html" if is_hx(request, "task-region") else "tasks.html"
    return templates.TemplateResponse(request=request, name=tpl, context=context)


@router.get("/tasks/partial")
def tasks_partial(
    request: Request,
    status: str = "",
    page: int = 1,
    size: int = TASK_PAGE_SIZE,
    source: str = "",
    date_from: str | None = None,
    date_to: str = "",
):
    denied = _deny_menu(request, "tasks")
    if denied:
        return denied
    date_from = _default_task_date(date_from)
    resolved_size = normalize_size(size, TASK_PAGE_SIZE)
    with SessionLocal() as db:
        if source in ("payment", "oa"):
            context = _payment_task_list_context(db, status, page, resolved_size, source=source)
        else:
            context = _task_list_context(db, status, page, resolved_size, date_from, date_to)
            context["task_source"] = "bank"
            context["source"] = ""
    return templates.TemplateResponse(request=request, name="tasks_partial.html", context=context)


CHANGE_ACTION_LABELS = {
    "payment_edit": "编辑付款单据",
    "payment_confirm": "确认收付款账户",
    "payment_review": "审核付款单据",
    "payment_change": "变更付款单据",
    "account_update": "修改付款账户",
    "account_delete": "删除付款账户",
    "payee_change": "修改收款账户",
    "payee_delete": "删除收款账户",
    "sub_account_create": "新增子账簿",
    "sub_account_status": "启停子账簿",
}

_CHANGE_MENUS = {
    "payment_doc": "payments",
    "bank_account": "master_data",
    "payee_account": "master_data",
    "sub_account": "master_data",
}


@router.get("/audit/history")
def audit_history(
    request: Request,
    target_type: str,
    target_id: str = "",
    page: int = 1,
    size: int = 10,
):
    user = getattr(request.state, "user", None)
    if user is None:
        return RedirectResponse("/", status_code=303)
    menu = _CHANGE_MENUS.get(target_type)
    if menu is not None:
        if not permissions.can_menu(user.role, menu):
            return RedirectResponse("/", status_code=303)
    elif user.role != "admin":
        return RedirectResponse("/", status_code=303)

    with SessionLocal() as db:
        result = list_changes(db, target_type, target_id, page, size)
    return templates.TemplateResponse(
        request=request,
        name="_change_history.html",
        context={
            "target_type": target_type,
            "target_id": target_id,
            "items": result["items"],
            "page_no": result["page"],
            "pages": result["pages"],
            "total": result["total"],
            "action_labels": CHANGE_ACTION_LABELS,
        },
    )


@router.get("/statements")
def statements_page(
    request: Request,
    account_id: str = "",
    date_from: str = "",
    date_to: str = "",
    q: str = "",
    direction: str = "",
    page: int = 1,
    size: int = 50,
):
    resolved_account_id = int(account_id) if account_id.isdigit() else None
    resolved_size = normalize_size(size, 50)
    page_no = max(page, 1)
    d_from = dt.date.fromisoformat(date_from) if date_from else None
    d_to = dt.date.fromisoformat(date_to) if date_to else None
    with SessionLocal() as db:
        result = data_api.list_statements(
            account_id=resolved_account_id,
            date_from=d_from,
            date_to=d_to,
            q=q or None,
            direction=direction or None,
            page=page_no,
            size=resolved_size,
            db=db,
        )
        pages = max(1, (result["total"] + resolved_size - 1) // resolved_size)
        if page_no > pages:
            page_no = pages
            result = data_api.list_statements(
                account_id=resolved_account_id,
                date_from=d_from,
                date_to=d_to,
                q=q or None,
                direction=direction or None,
                page=page_no,
                size=resolved_size,
                db=db,
            )
        accounts = accounts_api.list_accounts(db)["items"]
    ctx = {
        "page": "statements",
        "data": result,
        "accounts": accounts,
        "filters": {
            "account_id": resolved_account_id,
            "date_from": date_from,
            "date_to": date_to,
            "q": q,
            "direction": direction,
        },
    }
    tpl = "_statements_region.html" if is_hx(request, "statements-region") else "statements.html"
    return templates.TemplateResponse(request=request, name=tpl, context=ctx)


@router.get("/receipts")
def receipts_page(
    request: Request,
    account_id: str = "",
    date_from: str = "",
    date_to: str = "",
    q: str = "",
    page: int = 1,
    size: int = 50,
):
    resolved_account_id = int(account_id) if account_id.isdigit() else None
    resolved_size = normalize_size(size, 50)
    page_no = max(page, 1)
    d_from = dt.date.fromisoformat(date_from) if date_from else None
    d_to = dt.date.fromisoformat(date_to) if date_to else None
    with SessionLocal() as db:
        result = data_api.list_receipts(
            account_id=resolved_account_id,
            date_from=d_from,
            date_to=d_to,
            q=q or None,
            page=page_no,
            size=resolved_size,
            db=db,
        )
        pages = max(1, (result["total"] + resolved_size - 1) // resolved_size)
        if page_no > pages:
            page_no = pages
            result = data_api.list_receipts(
                account_id=resolved_account_id,
                date_from=d_from,
                date_to=d_to,
                q=q or None,
                page=page_no,
                size=resolved_size,
                db=db,
            )
        accounts = accounts_api.list_accounts(db)["items"]
    ctx = {
        "page": "receipts",
        "data": result,
        "accounts": accounts,
        "filters": {
            "account_id": resolved_account_id,
            "date_from": date_from,
            "date_to": date_to,
            "q": q,
        },
    }
    tpl = "_receipts_region.html" if is_hx(request, "receipts-region") else "receipts.html"
    return templates.TemplateResponse(request=request, name=tpl, context=ctx)


@router.get("/recon")
def recon_page(
    request: Request,
    batch_id: str = "",
    page: int = 1,
    size: int = 10,
    item_page: int = 1,
    item_size: int = 10,
):
    denied = _deny_menu(request, "recon")
    if denied:
        return denied
    resolved_batch_id = int(batch_id) if batch_id.isdigit() else None
    with SessionLocal() as db:
        batches = recon_api.list_batches(db)["items"]
        items = recon_api.batch_items(resolved_batch_id, db=db)["items"] if resolved_batch_id else []
        docs = recon_api.list_docs(limit=100, db=db)["items"]
    batch_pager = paginate_items(batches, page, size, default_size=10)
    item_pager = paginate_items(items, item_page, item_size, default_size=10)
    batch_params = {"size": batch_pager["size"], "batch_id": resolved_batch_id or ""}
    item_params = {"batch_id": resolved_batch_id or "", "item_size": item_pager["size"]}
    if batch_pager["page"] > 1:
        item_params["page"] = batch_pager["page"]
    if batch_pager["size"] != 10:
        item_params["size"] = batch_pager["size"]
    if item_pager["page"] > 1:
        batch_params["item_page"] = item_pager["page"]
    if item_pager["size"] != 10:
        batch_params["item_size"] = item_pager["size"]

    context = {
        "page": "recon",
        "batches": batches,
        "batch_rows": batch_pager["items"],
        "batch_pager": batch_pager,
        "batch_params": batch_params,
        "items": items,
        "item_rows": item_pager["items"],
        "item_pager": item_pager,
        "item_params": item_params,
        "docs": docs,
        "batch_id": resolved_batch_id,
    }
    if is_hx(request, "recon-batches-region"):
        return templates.TemplateResponse(request=request, name="_recon_batches_table.html", context=context)
    if is_hx(request, "recon-items-region"):
        return templates.TemplateResponse(request=request, name="_recon_items_table.html", context=context)
    return templates.TemplateResponse(
        request=request,
        name="recon.html",
        context=context,
    )


ACCOUNT_TABS = {"accounts", "devices", "sub"}


@router.get("/accounts")
def accounts_page(
    request: Request,
    tab: str = "accounts",
    page: int = 1,
    size: int = 10,
    dpage: int = 1,
    dsize: int = 10,
    dq: str = "",
    spage: int = 1,
    ssize: int = 10,
    q: str = "",
):
    if tab not in ACCOUNT_TABS:
        tab = "accounts"
    user = getattr(request.state, "user", None)
    role = user.role if user else "user"
    can_edit = permissions.can_edit_accounts(role)
    can_collect = permissions.can_collect_accounts(role)
    can_manage_devices = permissions.can_menu(role, "devices")
    can_master_data = permissions.can_menu(role, "master_data")
    if tab == "sub" and not can_master_data:
        tab = "accounts"

    with SessionLocal() as db:
        accounts = accounts_api.list_accounts(db)["items"]
        devices = accounts_api.list_devices(db)["items"]
        sub_data = sub_region(db)

    keyword = q.strip().lower()
    if keyword:
        accounts = [
            a
            for a in accounts
            if keyword
            in f"{a.get('account_no') or ''} {a.get('account_name') or ''}".lower()
        ]

    device_bindings = {
        account["device_id"]: f"{account['bank_code']} · {account['account_no']}"
        for account in accounts
        if account.get("device_id")
    }
    acct_pager = paginate_items(accounts, page, size, default_size=10)

    keyword = dq.strip().lower()
    filtered_devices = [
        d
        for d in devices
        if not keyword
        or keyword
        in " ".join(
            [
                str(d.get("name") or ""),
                bank_label(d.get("bank_code")),
                str(d.get("gf3") or ""),
                str(d.get("agent_name") or ""),
                str(device_bindings.get(d.get("id"), "")),
            ]
        ).lower()
    ]
    dev_pager = paginate_items(filtered_devices, dpage, dsize, default_size=10)
    sub_paged = paginate_items(sub_data["sub_rows"], spage, ssize, default_size=10)

    acct_params = {"tab": "accounts", "size": acct_pager["size"]}
    if q.strip():
        acct_params["q"] = q.strip()
    dev_params = {"tab": "devices", "dsize": dev_pager["size"], "dq": dq.strip()}
    sub_params = {"tab": "sub", "ssize": sub_paged["size"]}
    if acct_pager["page"] > 1:
        dev_params["page"] = acct_pager["page"]
        sub_params["page"] = acct_pager["page"]
    if acct_pager["size"] != 10:
        dev_params["size"] = acct_pager["size"]
        sub_params["size"] = acct_pager["size"]

    context = {
        "page": "accounts",
        "tab": tab,
        "q": q.strip(),
        "accounts": accounts,
        "account_rows": acct_pager["items"],
        "acct_pager": acct_pager,
        "acct_params": acct_params,
        "devices": devices,
        "device_rows": dev_pager["items"],
        "dev_pager": dev_pager,
        "dev_params": dev_params,
        "dq": dq.strip(),
        "device_bindings": device_bindings,
        "banks": list_banks(),
        "can_edit": can_edit,
        "can_collect": can_collect,
        "can_manage_devices": can_manage_devices,
        "can_master_data": can_master_data,
        "sub_page_rows": sub_paged["items"],
        "sub_pager": sub_paged,
        "sub_params": sub_params,
        "msg": request.query_params.get("msg", ""),
        **sub_data,
    }
    if is_hx(request, "accounts-table-region"):
        return templates.TemplateResponse(request=request, name="_accounts_table.html", context=context)
    if is_hx(request, "accounts-devices-region"):
        return templates.TemplateResponse(request=request, name="_accounts_devices_table.html", context=context)
    if is_hx(request, "sub-region"):
        return templates.TemplateResponse(request=request, name="_sub_accounts_panel.html", context=context)
    return templates.TemplateResponse(request=request, name="accounts.html", context=context)


@router.get("/devices")
def devices_page(
    request: Request,
    apage: int = 1,
    asize: int = 10,
):
    denied = _deny_menu(request, "devices")
    if denied:
        return denied
    with SessionLocal() as db:
        devices = accounts_api.list_devices(db)["items"]
        agents = accounts_api.list_agents(db)["items"]
        accounts = accounts_api.list_accounts(db)["items"]
        db_agents = db.execute(select(Agent)).scalars().all()
    for agent in agents:
        agent["device_count"] = sum(1 for device in devices if device["agent_code"] == agent["code"])
    user = getattr(request.state, "user", None)
    role = permissions.effective_role(user.role) if user else ""
    agent_tokens: dict[str, str] = {}
    if role == "admin":
        for token, codes in settings.agent_token_scopes.items():
            for code in codes:
                agent_tokens[code] = token
        known = sorted(settings.agent_tokens)
        if len(known) == 1:
            for agent in agents:
                agent_tokens.setdefault(agent["code"], known[0])
        for row in db_agents:
            if row.token_enc:
                token = security.decrypt(row.token_enc)
                if token:
                    agent_tokens.setdefault(row.code, token)
    today = dt.date.today()
    stats = {
        "agents": len(agents),
        "agents_online": sum(1 for agent in agents if agent["status"] == "online"),
        "devices": len(devices),
        "devices_online": sum(1 for device in devices if device["status"] == "online"),
        "expiring": sum(
            1
            for device in devices
            if device["cert_expires_at"] and (device["cert_expires_at"] - today).days <= 60
        ),
        "bound": sum(1 for account in accounts if account.get("device_id")),
    }
    agent_pager = paginate_items(agents, apage, asize, default_size=10)
    agent_params = {"asize": agent_pager["size"]}

    context = {
        "page": "devices",
        "devices": devices,
        "agents": agents,
        "agent_rows": agent_pager["items"],
        "agent_pager": agent_pager,
        "agent_params": agent_params,
        "stats": stats,
        "agent_tokens": agent_tokens,
        "agent_base_url": str(request.base_url).rstrip("/"),
        "is_admin": role == "admin",
    }
    if is_hx(request, "devices-agents-region"):
        return templates.TemplateResponse(request=request, name="_devices_agents_table.html", context=context)
    return templates.TemplateResponse(
        request=request,
        name="devices.html",
        context=context,
    )


SETTINGS_TABS = {"users", "system", "alerts"}
ROLE_LABELS = permissions.ROLE_LABELS


def _settings_redirect(tab: str, msg: str = "") -> RedirectResponse:
    target = f"/settings/{tab}"
    if msg:
        from urllib.parse import quote

        target += f"?msg={quote(msg)}"
    return RedirectResponse(target, status_code=303)


def _settings_admin(request: Request) -> AppUser:
    user = getattr(request.state, "user", None)
    if user is None or user.role != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以访问平台管理")
    return user


def _scoped_user(db, admin: AppUser, user_id: int) -> AppUser | None:
    query = select(AppUser).where(AppUser.id == user_id)
    if admin.tenant_id is not None:
        query = query.where(AppUser.tenant_id == admin.tenant_id)
    return db.execute(query).scalars().first()


def _render_settings(
    request: Request,
    tab: str,
    page: int = 1,
    size: int = 10,
    audit: str = "user",
):
    user = getattr(request.state, "user", None)
    if user is None or user.role != "admin":
        return RedirectResponse("/", status_code=303)
    if tab not in SETTINGS_TABS:
        tab = "users"
    if audit not in {"all", "user", "system"}:
        audit = "user"
    with SessionLocal() as db:
        user_query = select(AppUser).order_by(AppUser.id)
        if user.tenant_id is not None:
            user_query = user_query.where(AppUser.tenant_id == user.tenant_id)
        users = db.execute(user_query).scalars().all()
        log_query = select(AuditLog)
        if audit == "user":
            log_query = log_query.where(AuditLog.action.in_(_AUDIT_USER_ACTIONS))
        elif audit == "system":
            log_query = log_query.where(AuditLog.action.not_in(_AUDIT_USER_ACTIONS))
        log_pager = paginate_scalars(
            db,
            log_query.order_by(AuditLog.id.desc()),
            page,
            size,
            default_size=10,
        )
        action_counts = dict(
            db.execute(select(AuditLog.action, func.count()).group_by(AuditLog.action)).all()
        )
        user_total = sum(n for a, n in action_counts.items() if a in _AUDIT_USER_ACTIONS)
        all_total = sum(action_counts.values())
        devices = accounts_api.list_devices(db)["items"]
        alert_config = alerts.load_config(db)
        alert_pager = paginate_scalars(
            db,
            select(AlertEvent).order_by(AlertEvent.id.desc()),
            page,
            size,
            default_size=20,
        )
        alert_open = db.execute(
            select(func.count()).select_from(AlertEvent).where(AlertEvent.handled_at.is_(None))
        ).scalar() or 0
        backup_state = {
            "running": app_settings.get_bool(db, "backup_running", False),
            "last_at": app_settings.get_value(db, "backup_last_at", ""),
            "last_file": app_settings.get_value(db, "backup_last_file", ""),
            "last_size": app_settings.get_value(db, "backup_last_size", "0"),
            "last_error": app_settings.get_value(db, "backup_last_error", ""),
        }

    user_pager = paginate_items(users, page, size, default_size=10)

    log_params = {"size": log_pager["size"], "audit": audit}

    context = {
        "page": "settings",
        "tab": tab,
        "audit": audit,
        "audit_counts": {"all": all_total, "user": user_total, "system": all_total - user_total},
        "users": users,
        "user_rows": user_pager["items"],
        "user_pager": user_pager,
        "role_labels": ROLE_LABELS,
        "user_count": len(users),
        "admin_count": sum(1 for item in users if item.role == "admin"),
        "active_count": sum(1 for item in users if item.is_active),
        "logs": log_pager["items"],
        "log_rows": log_pager["items"],
        "log_pager": log_pager,
        "log_params": log_params,
        "devices": devices,
        "settings": settings,
        "alert_config": alert_config,
        "alert_events": alert_pager["items"],
        "alert_pager": alert_pager,
        "alert_open_count": alert_open,
        "alert_kind_labels": alerts.KIND_LABELS,
        "alert_status_labels": alerts.STATUS_LABELS,
        "backup_state": backup_state,
        "current_user_id": user.id,
        "msg": request.query_params.get("msg", ""),
    }
    if is_hx(request, "settings-users-region"):
        return templates.TemplateResponse(request=request, name="_settings_users_table.html", context=context)
    if is_hx(request, "settings-logs-region"):
        return templates.TemplateResponse(request=request, name="_settings_logs_table.html", context=context)
    if is_hx(request, "settings-alerts-region") and tab == "alerts":
        return templates.TemplateResponse(request=request, name="_settings_alerts.html", context=context)
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=context,
    )


def _deny_menu(request: Request, key: str):
    user = getattr(request.state, "user", None)
    if user is not None and permissions.can_menu(user.role, key):
        return None
    return RedirectResponse("/", status_code=303)


@router.get("/settings")
def settings_page(request: Request, tab: str = "users", page: int = 1, size: int = 10, audit: str = "user"):
    return _render_settings(request, tab, page=page, size=size, audit=audit)


@router.get("/settings/users")
def settings_users_page(request: Request, page: int = 1, size: int = 10):
    return _render_settings(request, "users", page=page, size=size)


@router.get("/settings/system")
def settings_system_page(request: Request, page: int = 1, size: int = 10, audit: str = "user"):
    return _render_settings(request, "system", page=page, size=size, audit=audit)


@router.get("/settings/alerts")
def settings_alerts_page(request: Request):
    return _render_settings(request, "alerts")


@router.post("/settings/backup/now")
def settings_backup_now(request: Request):
    _settings_admin(request)
    with SessionLocal() as db:
        if app_settings.get_bool(db, "backup_running", False):
            return _settings_redirect("system", "备份正在进行中")
    threading.Thread(target=run_backup_job, daemon=True).start()
    return _settings_redirect("system", "备份任务已开始，稍后刷新查看结果")


@router.post("/settings/alerts")
def settings_alerts_save(
    request: Request,
    enabled: str = Form("0"),
    webhook: str = Form(""),
    device_offline_minutes: str = Form("30"),
    account_stale_days: str = Form("2"),
    cooldown_hours: str = Form("6"),
    quiet_hours: str = Form(""),
):
    admin = _settings_admin(request)
    webhook = webhook.strip()
    quiet_hours = quiet_hours.strip()
    if webhook and not webhook.startswith(("http://", "https://")):
        return _settings_redirect("alerts", "Webhook 地址需以 http:// 或 https:// 开头")
    if not alerts.valid_quiet_hours(quiet_hours):
        return _settings_redirect("alerts", "静默时段格式应为 22:00-07:00")

    def _positive(text: str, fallback: int) -> int:
        try:
            value = int(text.strip())
        except ValueError:
            return fallback
        return value if value > 0 else fallback

    offline = _positive(device_offline_minutes, 30)
    stale = _positive(account_stale_days, 2)
    cooldown = _positive(cooldown_hours, 6)
    with SessionLocal() as db:
        app_settings.set_value(db, "alert_enabled", "1" if enabled == "1" else "0")
        app_settings.set_value(db, "alert_webhook", webhook)
        app_settings.set_value(db, "alert_device_offline_minutes", str(offline))
        app_settings.set_value(db, "alert_account_stale_days", str(stale))
        app_settings.set_value(db, "alert_cooldown_hours", str(cooldown))
        app_settings.set_value(db, "alert_quiet_hours", quiet_hours)
        db.add(
            AuditLog(
                actor=admin.username,
                action="alert_settings",
                target_type="app_setting",
                target_id="alerts",
                detail={"enabled": enabled == "1", "webhook_configured": bool(webhook)},
            )
        )
        db.commit()
    return _settings_redirect("alerts", "告警设置已保存")


@router.post("/settings/alerts/test")
def settings_alerts_test(request: Request):
    _settings_admin(request)
    with SessionLocal() as db:
        cfg = alerts.load_config(db)
    ok, error = alerts.send_test(cfg)
    return _settings_redirect("alerts", "测试消息已发送" if ok else f"测试发送失败：{error}")


@router.post("/settings/alerts/{event_id}/handle")
def settings_alert_handle(request: Request, event_id: int):
    admin = _settings_admin(request)
    with SessionLocal() as db:
        event = db.get(AlertEvent, event_id)
        if event is None:
            return _settings_redirect("alerts", "告警不存在")
        event.handled_at = dt.datetime.now()
        event.handled_by = admin.username
        db.commit()
    return _settings_redirect("alerts", "告警已标记处理")


@router.post("/settings/users")
def settings_create_user(
    request: Request,
    username: str = Form(...),
    display_name: str = Form(""),
    password: str = Form(...),
    role: str = Form("user"),
    tenant_id: str = Form(""),
):
    admin = _settings_admin(request)
    username = username.strip()
    display_name = display_name.strip() or username
    if len(username) < 2:
        return _settings_redirect("users", "登录账户至少 2 个字符")
    if any(ch.isspace() for ch in username):
        return _settings_redirect("users", "登录账户不能包含空格")
    error = security.validate_password(password, username)
    if error:
        return _settings_redirect("users", error)
    role = role if role in ROLE_LABELS else "user"
    target_tenant = admin.tenant_id
    if target_tenant is None and tenant_id.isdigit():
        target_tenant = int(tenant_id)
    with SessionLocal() as db:
        if db.execute(select(AppUser).where(AppUser.username == username)).scalars().first():
            return _settings_redirect("users", f"登录账户 {username} 已存在")
        user = AppUser(
            username=username,
            display_name=display_name,
            password_hash=security.hash_password(password),
            role=role,
            tenant_id=target_tenant,
            is_active=True,
        )
        db.add(user)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            return _settings_redirect("users", f"登录账户 {username} 已存在")
        db.add(
            AuditLog(
                actor=admin.username,
                action="user_create",
                target_type="app_user",
                target_id=str(user.id),
                detail={"username": username, "display_name": display_name, "role": ROLE_LABELS[role], "tenant_id": target_tenant},
            )
        )
        db.commit()
    return _settings_redirect("users", f"用户 {display_name} 已创建，登录账户 {username}")


@router.post("/settings/users/{user_id}/role")
def settings_update_user_role(request: Request, user_id: int, role: str = Form(...)):
    admin = _settings_admin(request)
    if role not in ROLE_LABELS:
        return _settings_redirect("users", "无效的用户角色")
    with SessionLocal() as db:
        target = _scoped_user(db, admin, user_id)
        if target is None:
            return _settings_redirect("users", "用户不存在")
        if target.id == admin.id and role != "admin":
            return _settings_redirect("users", "不能修改当前登录用户的管理员角色")
        username = target.username
        target.role = role
        db.add(
            AuditLog(
                actor=admin.username,
                action="user_role",
                target_type="app_user",
                target_id=str(target.id),
                detail={"username": username, "role": ROLE_LABELS[role]},
            )
        )
        db.commit()
    return _settings_redirect("users", f"用户 {username} 的角色已更新")


@router.post("/settings/users/{user_id}/edit")
def settings_update_user(
    request: Request,
    user_id: int,
    display_name: str = Form(""),
    username: str = Form(""),
    role: str = Form(...),
    tenant_id: str = Form(""),
    password: str = Form(""),
    is_active: str = Form("1"),
):
    admin = _settings_admin(request)
    display_name = display_name.strip()
    username = username.strip()
    if not display_name:
        return _settings_redirect("users", "姓名不能为空")
    if len(username) < 2 or any(ch.isspace() for ch in username):
        return _settings_redirect("users", "登录账户至少 2 个字符且不能包含空格")
    if password:
        error = security.validate_password(password, username)
        if error:
            return _settings_redirect("users", error)
    if role not in ROLE_LABELS:
        return _settings_redirect("users", "无效的用户角色")
    with SessionLocal() as db:
        target = _scoped_user(db, admin, user_id)
        if target is None:
            return _settings_redirect("users", "用户不存在")
        if target.id == admin.id:
            if role != "admin":
                return _settings_redirect("users", "不能修改当前登录用户的管理员角色")
            if username != target.username:
                return _settings_redirect("users", "不能修改当前登录用户的登录账户")
            if is_active != "1":
                return _settings_redirect("users", "不能停用当前登录用户")
        dup = db.execute(
            select(AppUser).where(AppUser.username == username, AppUser.id != target.id)
        ).scalars().first()
        if dup is not None:
            return _settings_redirect("users", f"登录账户 {username} 已存在")
        target.display_name = display_name
        target.username = username
        target.role = role
        target.is_active = is_active == "1"
        if password:
            target.password_hash = security.hash_password(password)
            attempt = db.execute(
                select(LoginAttempt).where(LoginAttempt.username == target.username)
            ).scalars().first()
            if attempt is not None:
                db.delete(attempt)
        if admin.tenant_id is None and tenant_id.isdigit():
            target.tenant_id = int(tenant_id)
        db.add(
            AuditLog(
                actor=admin.username,
                action="user_edit",
                target_type="app_user",
                target_id=str(target.id),
                detail={
                    "username": username,
                    "display_name": display_name,
                    "role": ROLE_LABELS[role],
                    "is_active": target.is_active,
                    "password_reset": bool(password),
                },
            )
        )
        db.commit()
    return _settings_redirect("users", f"用户 {display_name} 的资料已更新")
