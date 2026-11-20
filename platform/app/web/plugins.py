"""采集插件管理（平台管理 → 采集插件）与失败现场诊断查看页面。"""
from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Agent, CollectionTask, PaymentDraftTask, TaskDiagnostic
from app.services import permissions
from app.services.diagnostics import (
    ERROR_CODE_LABELS,
    list_diagnostics,
    resolve_diagnostic_path,
)
from app.services.plugins import (
    PluginError,
    activate_release,
    desired_plugins_for_channel,
    list_releases,
    set_agent_channel,
    upload_plugin_release,
)
from app.web.routes import templates

router = APIRouter()


def _require_admin(request: Request):
    user = getattr(request.state, "user", None)
    if user is None or permissions.effective_role(user.role) != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可执行此操作")
    return user


def _plugins_redirect(msg: str = "") -> RedirectResponse:
    url = "/settings/plugins"
    if msg:
        url += f"?msg={quote(msg)}"
    return RedirectResponse(url, status_code=303)


@router.get("/settings/plugins")
def settings_plugins_page(request: Request, msg: str = ""):
    _require_admin(request)
    with SessionLocal() as db:
        releases = list_releases(db)
        agents = db.execute(select(Agent).order_by(Agent.code.asc())).scalars().all()
        desired_prod = desired_plugins_for_channel(db, "prod")
        desired_test = desired_plugins_for_channel(db, "test")

        agent_rows = []
        for agent in agents:
            channel = agent.channel or "prod"
            desired_map = desired_test if channel == "test" else desired_prod
            installed = (agent.capabilities or {}).get("plugins") or {}
            if not isinstance(installed, dict):
                installed = {}
            statuses = []
            for pid, spec in sorted(desired_map.items()):
                expected_ver = spec.get("version") or ""
                actual_ver = str(installed.get(pid) or "")
                statuses.append(
                    {
                        "plugin_id": pid,
                        "expected": expected_ver,
                        "actual": actual_ver,
                        "synced": bool(expected_ver and actual_ver == expected_ver),
                    }
                )
            agent_rows.append(
                {
                    "id": agent.id,
                    "code": agent.code,
                    "name": agent.name,
                    "status": agent.status,
                    "channel": channel,
                    "last_heartbeat": agent.last_heartbeat,
                    "installed": installed,
                    "plugin_statuses": statuses,
                }
            )

    return templates.TemplateResponse(
        request=request,
        name="settings_plugins.html",
        context={
            "page": "settings",
            "tab": "plugins",
            "msg": msg,
            "releases": releases,
            "agents": agent_rows,
            "has_public_key": bool(settings.plugin_public_key),
            "desired_prod_count": len(desired_prod),
            "desired_test_count": len(desired_test),
        },
    )


@router.post("/settings/plugins/upload")
async def settings_plugins_upload(
    request: Request,
    file: UploadFile = File(...),
    signature: str = Form(""),
    changelog: str = Form(""),
):
    admin = _require_admin(request)
    content = await file.read()
    with SessionLocal() as db:
        try:
            row = upload_plugin_release(
                db,
                content=content,
                signature=signature,
                changelog=changelog,
                operator=admin.username,
            )
        except PluginError as exc:
            return _plugins_redirect(f"上传失败：{exc}")
    return _plugins_redirect(f"插件 {row.plugin_id} v{row.version} 已入库，可先推送到测试机验证")


@router.post("/settings/plugins/{release_id}/activate")
def settings_plugins_activate(
    request: Request,
    release_id: int,
    channel: str = Form(...),
):
    admin = _require_admin(request)
    with SessionLocal() as db:
        try:
            row = activate_release(db, release_id, channel, operator=admin.username)
        except PluginError as exc:
            return _plugins_redirect(f"切换失败：{exc}")
    label = "测试机（test）" if channel == "test" else "正式机（prod）"
    return _plugins_redirect(f"{row.plugin_id} v{row.version} 已设为{label}生效版本")


@router.post("/settings/plugins/agents/{agent_id}/channel")
def settings_plugins_agent_channel(
    request: Request,
    agent_id: int,
    channel: str = Form(...),
):
    admin = _require_admin(request)
    with SessionLocal() as db:
        try:
            agent = set_agent_channel(db, agent_id, channel, operator=admin.username)
        except PluginError as exc:
            return _plugins_redirect(f"切换失败：{exc}")
    label = "测试机" if channel == "test" else "正式机"
    return _plugins_redirect(f"执行机 {agent.name}（{agent.code}）已切为{label}通道")


@router.get("/tasks/{task_id}/diagnostics")
def task_diagnostics_page(request: Request, task_id: int, task_kind: str = "collection"):
    kind = "draft" if task_kind == "draft" else "collection"
    with SessionLocal() as db:
        if kind == "draft":
            task = db.get(PaymentDraftTask, task_id)
        else:
            task = db.get(CollectionTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        rows = list_diagnostics(db, kind, task_id)
        items = []
        meta_list = []
        for row in rows:
            preview_text = ""
            if row.kind in ("dom", "meta", "log"):
                try:
                    path = resolve_diagnostic_path(row)
                    if path.is_file():
                        raw_text = path.read_text(encoding="utf-8", errors="replace")
                        if row.kind == "meta":
                            try:
                                meta_list.append(json.loads(raw_text))
                            except json.JSONDecodeError:
                                preview_text = raw_text[:20000]
                        else:
                            preview_text = raw_text[:60000]
                except Exception:
                    preview_text = ""
            items.append(
                {
                    "id": row.id,
                    "kind": row.kind,
                    "attempt": row.attempt,
                    "error_code": row.error_code,
                    "error_label": ERROR_CODE_LABELS.get(row.error_code, row.error_code),
                    "stage": row.stage,
                    "name": row.original_name,
                    "size": row.size,
                    "created_at": row.created_at,
                    "preview_text": preview_text,
                }
            )

    return templates.TemplateResponse(
        request=request,
        name="task_diagnostics.html",
        context={
            "page": "payment_drafts" if kind == "draft" else "tasks",
            "task_kind": kind,
            "task_id": task_id,
            "task": task,
            "error_label": ERROR_CODE_LABELS.get(getattr(task, "error_code", "") or "", ""),
            "items": items,
            "meta_list": meta_list,
            "back_url": "/payment-drafts" if kind == "draft" else "/tasks",
        },
    )


@router.get("/diagnostics/{diag_id}/raw")
def task_diagnostic_raw(request: Request, diag_id: int, download: int = 0):
    with SessionLocal() as db:
        row = db.get(TaskDiagnostic, diag_id)
        if row is None:
            raise HTTPException(status_code=404, detail="诊断文件不存在")
        path = resolve_diagnostic_path(row)
        filename = row.original_name or path.name
        kind = row.kind
        content_type = row.content_type
    if not path.is_file():
        raise HTTPException(status_code=404, detail="诊断文件已清理或丢失")
    # 严防 HTML 文件在同源浏览器直接渲染导致 XSS：非图片一律以附件下载或纯文本返回
    if kind == "screenshot" and not download:
        return FileResponse(str(path), media_type=content_type or "image/jpeg")
    media = "text/plain; charset=utf-8" if kind in ("dom", "meta", "log") and not download else "application/octet-stream"
    if download or kind not in ("screenshot", "dom", "meta", "log"):
        return FileResponse(str(path), filename=filename, media_type="application/octet-stream")
    return FileResponse(str(path), media_type=media)
