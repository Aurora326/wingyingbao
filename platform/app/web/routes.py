from __future__ import annotations

import datetime as dt
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app.api import accounts as accounts_api
from app.api import dashboard as dashboard_api
from app.api import data as data_api
from app.api import recon as recon_api
from app.api import tasks as tasks_api
from app.config import settings
from app.db import SessionLocal
from app.models import AppUser, AuditLog, BankAccount, CollectionTask
from app.services import security
from collector.drivers.registry import list_banks

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


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
    "usb_robot_sync": "同步 UKey 设备",
    "agent_device_report": "客户侧 Agent 上报设备",
    "account_create": "创建银行账户",
    "credential_update": "更新登录信息",
}
_AUDIT_TARGETS = {
    "collection_task": "采集任务",
    "ukey_device": "UKey 设备",
    "bank_account": "银行账户",
    "agent": "采集 Agent",
}
_AUDIT_STATUS = {"succeeded": "已完成", "failed": "未完成", "running": "进行中"}
_AUDIT_ERRORS = {
    "LOGIN_FAILED": "登录失败",
    "SELECTOR_NOT_FOUND": "页面元素未找到",
    "AUTH_FAILED": "认证失败",
    "UKEY_NOT_FOUND": "未检测到 UKey",
    "TIMEOUT": "操作超时",
    "NETWORK_ERROR": "网络异常",
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
        rows.append(("附件", f"{len(detail.get('artifacts') or [])} 个文件"))
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


@router.get("/tasks")
def tasks_page(request: Request, status: str = ""):
    with SessionLocal() as db:
        items = tasks_api.list_tasks(status=status or None, limit=100, db=db)["items"]
        accounts = accounts_api.list_accounts(db)["items"]
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
        "last": last_task,
    }
    return templates.TemplateResponse(
        request=request,
        name="tasks.html",
        context={"page": "tasks", "tasks": items, "accounts": accounts, "status": status, "stats": stats},
    )


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


@router.get("/settings")
def settings_page(request: Request):
    with SessionLocal() as db:
        logs = db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(50)).scalars().all()
        devices = accounts_api.list_devices(db)["items"]
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "page": "settings",
            "logs": logs,
            "devices": devices,
            "settings": settings,
        },
    )
