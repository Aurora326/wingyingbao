from __future__ import annotations

import datetime as dt
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
from app.models import AppUser, AuditLog, BankAccount, CollectionTask, PaymentCollectTask, Tenant
from app.services import collectors, security
from app.services import permissions
from app.services.banks import list_banks
from app.services.payment_collect import task_dict

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
_STATIC_DIR = Path(__file__).resolve().parent / "static"


def _asset_version() -> str:
    files = [_STATIC_DIR / name for name in ("app.css", "app.js", "ui.js")]
    try:
        return str(int(max(path.stat().st_mtime for path in files)))
    except OSError:
        return "1"


templates.env.globals["asset_version"] = _asset_version
templates.env.globals["can_menu"] = permissions.can_menu
templates.env.globals["can_edit_accounts"] = permissions.can_edit_accounts
templates.env.globals["can_collect_accounts"] = permissions.can_collect_accounts
templates.env.globals["role_label"] = permissions.role_label


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
    "user_role": "调整用户角色",
    "user_toggle": "启用或停用用户",
    "user_password": "重置用户密码",
}
_AUDIT_TARGETS = {
    "collection_task": "采集任务",
    "ukey_device": "UKey 设备",
    "bank_account": "银行账户",
    "agent": "采集 Agent",
    "app_user": "平台用户",
}
_AUDIT_STATUS = {"succeeded": "已完成", "failed": "未完成", "running": "进行中", "cancelled": "已停止"}
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
    with SessionLocal() as db:
        user = db.execute(select(AppUser).where(AppUser.username == username)).scalars().first()
        if user is None or not security.verify_password(password, user.password_hash):
            return templates.TemplateResponse(
                request=request, name="login.html", context={"error": "用户名或密码错误"}, status_code=401
            )
    response = RedirectResponse("/", status_code=303)
    response.set_cookie("session", security.create_session(username), httponly=True, samesite="lax")
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


def _task_list_context(db, status: str, page: int = 1, size: int = TASK_PAGE_SIZE) -> dict:
    page = max(page, 1)
    result = tasks_api.list_tasks(
        status=status or None,
        limit=size,
        offset=(page - 1) * size,
        db=db,
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
        )
    status_counts = dict(
        db.execute(
            select(CollectionTask.status, func.count(CollectionTask.id)).group_by(CollectionTask.status)
        ).all()
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
    return {
        "tasks": result["items"],
        "status": status,
        "stats": stats,
        "page_no": page,
        "pages": pages,
        "total": total,
        "size": size,
    }


def _payment_task_list_context(db, status: str, page: int = 1, size: int = 20) -> dict:
    page = max(page, 1)
    query = select(PaymentCollectTask)
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
            select(PaymentCollectTask.status, func.count(PaymentCollectTask.id)).group_by(PaymentCollectTask.status)
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
        "status": status,
        "stats": stats,
        "page_no": page,
        "pages": pages,
        "total": total,
        "size": size,
        "task_source": "payment",
        "source": "payment",
    }


@router.get("/tasks")
def tasks_page(request: Request, status: str = "", page: int = 1, source: str = ""):
    denied = _deny_menu(request, "tasks")
    if denied:
        return denied
    with SessionLocal() as db:
        if source == "payment":
            context = _payment_task_list_context(db, status, page)
        else:
            context = _task_list_context(db, status, page)
            context["accounts"] = accounts_api.list_accounts(db)["items"]
            context["task_source"] = "bank"
            context["source"] = ""
    context.update(page="tasks")
    return templates.TemplateResponse(request=request, name="tasks.html", context=context)


@router.get("/tasks/partial")
def tasks_partial(request: Request, status: str = "", page: int = 1, source: str = ""):
    denied = _deny_menu(request, "tasks")
    if denied:
        return denied
    with SessionLocal() as db:
        if source == "payment":
            context = _payment_task_list_context(db, status, page)
        else:
            context = _task_list_context(db, status, page)
            context["task_source"] = "bank"
            context["source"] = ""
    return templates.TemplateResponse(request=request, name="tasks_partial.html", context=context)


@router.get("/statements")
def statements_page(
    request: Request,
    account_id: int | None = None,
    date_from: str = "",
    date_to: str = "",
    q: str = "",
    direction: str = "",
    page: int = 1,
):
    with SessionLocal() as db:
        result = data_api.list_statements(
            account_id=account_id,
            date_from=dt.date.fromisoformat(date_from) if date_from else None,
            date_to=dt.date.fromisoformat(date_to) if date_to else None,
            q=q or None,
            direction=direction or None,
            page=page,
            size=50,
            db=db,
        )
        accounts = accounts_api.list_accounts(db)["items"]
    return templates.TemplateResponse(
        request=request,
        name="statements.html",
        context={
            "page": "statements",
            "data": result,
            "accounts": accounts,
            "filters": {"account_id": account_id, "date_from": date_from, "date_to": date_to, "q": q, "direction": direction},
        },
    )


@router.get("/receipts")
def receipts_page(
    request: Request,
    account_id: int | None = None,
    date_from: str = "",
    date_to: str = "",
    q: str = "",
    page: int = 1,
):
    with SessionLocal() as db:
        result = data_api.list_receipts(
            account_id=account_id,
            date_from=dt.date.fromisoformat(date_from) if date_from else None,
            date_to=dt.date.fromisoformat(date_to) if date_to else None,
            q=q or None,
            page=page,
            size=50,
            db=db,
        )
        accounts = accounts_api.list_accounts(db)["items"]
    return templates.TemplateResponse(
        request=request,
        name="receipts.html",
        context={
            "page": "receipts",
            "data": result,
            "accounts": accounts,
            "filters": {"account_id": account_id, "date_from": date_from, "date_to": date_to, "q": q},
        },
    )


@router.get("/recon")
def recon_page(request: Request, batch_id: int | None = None):
    denied = _deny_menu(request, "recon")
    if denied:
        return denied
    with SessionLocal() as db:
        batches = recon_api.list_batches(db)["items"]
        items = recon_api.batch_items(batch_id, db=db)["items"] if batch_id else []
        docs = recon_api.list_docs(limit=100, db=db)["items"]
    return templates.TemplateResponse(
        request=request,
        name="recon.html",
        context={"page": "recon", "batches": batches, "items": items, "docs": docs, "batch_id": batch_id},
    )


@router.get("/accounts")
def accounts_page(request: Request):
    with SessionLocal() as db:
        accounts = accounts_api.list_accounts(db)["items"]
        devices = accounts_api.list_devices(db)["items"]
    device_bindings = {
        account["device_id"]: f"{account['bank_code']} · {account['account_no']}"
        for account in accounts
        if account.get("device_id")
    }
    return templates.TemplateResponse(
        request=request,
        name="accounts.html",
        context={
            "page": "accounts",
            "accounts": accounts,
            "devices": devices,
            "device_bindings": device_bindings,
            "banks": list_banks(),
        },
    )


@router.get("/devices")
def devices_page(request: Request):
    denied = _deny_menu(request, "devices")
    if denied:
        return denied
    with SessionLocal() as db:
        devices = accounts_api.list_devices(db)["items"]
        agents = accounts_api.list_agents(db)["items"]
        accounts = accounts_api.list_accounts(db)["items"]
    for agent in agents:
        agent["device_count"] = sum(1 for device in devices if device["agent_code"] == agent["code"])
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
    return templates.TemplateResponse(
        request=request,
        name="devices.html",
        context={"page": "devices", "devices": devices, "agents": agents, "stats": stats},
    )


SETTINGS_TABS = {"users", "system"}
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


def _render_settings(request: Request, tab: str):
    user = getattr(request.state, "user", None)
    if user is None or user.role != "admin":
        return RedirectResponse("/", status_code=303)
    if tab not in SETTINGS_TABS:
        tab = "users"
    with SessionLocal() as db:
        user_query = select(AppUser).order_by(AppUser.id)
        if user.tenant_id is not None:
            user_query = user_query.where(AppUser.tenant_id == user.tenant_id)
        users = db.execute(user_query).scalars().all()
        logs = db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(50)).scalars().all()
        devices = accounts_api.list_devices(db)["items"]
        tenants = db.execute(select(Tenant).order_by(Tenant.id)).scalars().all()
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "page": "settings",
            "tab": tab,
            "users": users,
            "tenant_map": {t.id: t.name for t in tenants},
            "role_labels": ROLE_LABELS,
            "user_count": len(users),
            "admin_count": sum(1 for item in users if item.role == "admin"),
            "active_count": sum(1 for item in users if item.is_active),
            "logs": logs,
            "devices": devices,
            "collectors": collectors.status_rows(),
            "collectors_file": str(settings.collectors_file),
            "settings": settings,
            "tenants": tenants,
            "is_super_admin": user.tenant_id is None,
            "current_tenant_id": getattr(request.state, "tenant_id", None),
            "current_user_id": user.id,
            "msg": request.query_params.get("msg", ""),
        },
    )


def _deny_menu(request: Request, key: str):
    user = getattr(request.state, "user", None)
    if user is not None and permissions.can_menu(user.role, key):
        return None
    return RedirectResponse("/", status_code=303)


@router.get("/settings")
def settings_page(request: Request, tab: str = "users"):
    return _render_settings(request, tab)


@router.get("/settings/users")
def settings_users_page(request: Request):
    return _render_settings(request, "users")


@router.get("/settings/system")
def settings_system_page(request: Request):
    return _render_settings(request, "system")


@router.post("/settings/users")
def settings_create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("user"),
    tenant_id: str = Form(""),
):
    admin = _settings_admin(request)
    username = username.strip()
    if len(username) < 2:
        return _settings_redirect("users", "用户名至少 2 个字符")
    if len(password) < 8:
        return _settings_redirect("users", "密码至少 8 个字符")
    role = role if role in ROLE_LABELS else "user"
    target_tenant = admin.tenant_id
    if target_tenant is None and tenant_id.isdigit():
        target_tenant = int(tenant_id)
    with SessionLocal() as db:
        if db.execute(select(AppUser).where(AppUser.username == username)).scalars().first():
            return _settings_redirect("users", f"用户名 {username} 已存在")
        user = AppUser(
            username=username,
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
            return _settings_redirect("users", f"用户名 {username} 已存在")
        db.add(
            AuditLog(
                actor=admin.username,
                action="user_create",
                target_type="app_user",
                target_id=str(user.id),
                detail={"username": username, "role": ROLE_LABELS[role], "tenant_id": target_tenant},
            )
        )
        db.commit()
    return _settings_redirect("users", f"用户 {username} 已创建")


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


@router.post("/settings/users/{user_id}/toggle")
def settings_toggle_user(request: Request, user_id: int):
    admin = _settings_admin(request)
    with SessionLocal() as db:
        target = _scoped_user(db, admin, user_id)
        if target is None:
            return _settings_redirect("users", "用户不存在")
        if target.id == admin.id:
            return _settings_redirect("users", "不能停用当前登录用户")
        username = target.username
        target.is_active = not target.is_active
        new_state = target.is_active
        db.add(
            AuditLog(
                actor=admin.username,
                action="user_toggle",
                target_type="app_user",
                target_id=str(target.id),
                detail={"username": username, "is_active": new_state},
            )
        )
        db.commit()
    return _settings_redirect("users", f"用户 {username} 已{'启用' if new_state else '停用'}")


@router.post("/settings/users/{user_id}/password")
def settings_reset_user_password(request: Request, user_id: int, password: str = Form(...)):
    admin = _settings_admin(request)
    if len(password) < 8:
        return _settings_redirect("users", "密码至少 8 个字符")
    with SessionLocal() as db:
        target = _scoped_user(db, admin, user_id)
        if target is None:
            return _settings_redirect("users", "用户不存在")
        username = target.username
        target.password_hash = security.hash_password(password)
        db.add(
            AuditLog(
                actor=admin.username,
                action="user_password",
                target_type="app_user",
                target_id=str(target.id),
                detail={"username": username},
            )
        )
        db.commit()
    return _settings_redirect("users", f"用户 {username} 的密码已重置")
