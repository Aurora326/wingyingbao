from __future__ import annotations

import hashlib
import hmac
from contextlib import contextmanager

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Agent, CollectionTask, PaymentDraftTask, PluginRelease
from app.schemas import AgentDeviceReport, AgentTaskClaim, AgentTaskResult
from app.services.agent_report import AgentReportError, apply_device_report
from app.services.agent_tasks import (
    AgentTaskError,
    apply_agent_task_result,
    claim_agent_task,
    get_agent_task_credentials,
    get_agent_task_status,
)
from app.services.diagnostics import DiagnosticError, save_diagnostic_file
from app.services.payment_collect import (
    PaymentCollectError,
    apply_payment_task_result,
    claim_payment_task,
)
from app.services.payment_draft import (
    DraftError,
    apply_draft_task_result,
    claim_draft_task,
)
from app.services.plugins import (
    PluginError,
    desired_plugins_for_agent,
    resolve_release_path,
)
from app.tenancy import tenant_context

router = APIRouter(prefix="/api/agent", tags=["agent"])


@contextmanager
def _agent_tenant(db: Session, agent_code: str):
    agent = db.execute(select(Agent).where(Agent.code == agent_code)).scalars().first()
    mapping = getattr(settings, "agent_tenants", {})
    tenant_id = (agent.tenant_id if agent else None) or mapping.get(agent_code)
    if agent is None and tenant_id is None and mapping:
        raise HTTPException(status_code=403, detail="Agent 未绑定租户")
    with tenant_context(tenant_id):
        yield


def _token_scopes() -> dict[str, set[str]]:
    value = getattr(settings, "agent_token_scopes", {})
    return value if isinstance(value, dict) else {}


def _token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _db_agent(db: Session, token: str) -> Agent | None:
    if not token:
        return None
    hashed = _token_hash(token)
    return db.execute(select(Agent).where(Agent.token_hash == hashed)).scalars().first()


def require_agent_token(
    authorization: str | None = Header(default=None),
    x_agent_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> str:
    tokens = set(getattr(settings, "agent_tokens", set()))
    tokens.update(_token_scopes())
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_agent_token:
        token = x_agent_token.strip()
    if token in tokens:
        return token
    if _db_agent(db, token) is not None:
        return token
    raise HTTPException(status_code=401, detail="Agent 令牌无效")


def _authorize_agent(agent_code: str, token: str, db: Session) -> str:
    code = (agent_code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="缺少 agent_code")
    agent = db.execute(select(Agent).where(Agent.code == code)).scalars().first()
    if agent is not None and agent.token_hash and hmac.compare_digest(agent.token_hash, _token_hash(token)):
        return code
    scopes = _token_scopes()
    allowed = scopes.get(token)
    if allowed is not None:
        if code not in allowed:
            raise HTTPException(status_code=403, detail="令牌无权操作该 Agent")
    elif scopes:
        raise HTTPException(status_code=403, detail="令牌无权操作该 Agent")
    return code


@router.get("/ping")
def ping(_: str = Depends(require_agent_token)):
    return {"ok": True}


@router.post("/devices/report")
def report_devices(
    payload: AgentDeviceReport,
    token: str = Depends(require_agent_token),
    db: Session = Depends(get_db),
):
    agent_code = _authorize_agent(payload.agent_code, token, db)
    with _agent_tenant(db, agent_code):
        try:
            return apply_device_report(db, payload)
        except AgentReportError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@router.post("/tasks/claim")
def claim_task(
    payload: AgentTaskClaim,
    token: str = Depends(require_agent_token),
    db: Session = Depends(get_db),
):
    agent_code = _authorize_agent(payload.agent_code, token, db)
    with _agent_tenant(db, agent_code):
        return {"ok": True, "task": claim_agent_task(db, agent_code)}


@router.get("/tasks/{task_id}/status")
def task_status(
    task_id: int,
    agent_code: str,
    token: str = Depends(require_agent_token),
    db: Session = Depends(get_db),
):
    code = _authorize_agent(agent_code, token, db)
    with _agent_tenant(db, code):
        try:
            return get_agent_task_status(db, task_id, code)
        except AgentTaskError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/tasks/{task_id}/credentials")
def task_credentials(
    task_id: int,
    agent_code: str,
    attempt: int | None = None,
    token: str = Depends(require_agent_token),
    db: Session = Depends(get_db),
):
    code = _authorize_agent(agent_code, token, db)
    with _agent_tenant(db, code):
        try:
            return get_agent_task_credentials(db, task_id, code, attempt)
        except AgentTaskError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.post("/tasks/{task_id}/result")
def report_task_result(
    task_id: int,
    payload: AgentTaskResult,
    token: str = Depends(require_agent_token),
    db: Session = Depends(get_db),
):
    agent_code = _authorize_agent(payload.agent_code, token, db)
    payload.agent_code = agent_code
    with _agent_tenant(db, agent_code):
        try:
            return apply_agent_task_result(db, task_id, payload)
        except AgentTaskError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.post("/payment-tasks/claim")
def claim_payment_task_api(
    payload: AgentTaskClaim,
    token: str = Depends(require_agent_token),
    db: Session = Depends(get_db),
):
    agent_code = _authorize_agent(payload.agent_code, token, db)
    with _agent_tenant(db, agent_code):
        return {"ok": True, "task": claim_payment_task(db, agent_code)}


@router.post("/payment-tasks/{task_id}/result")
def report_payment_task_result(
    task_id: int,
    payload: AgentTaskResult,
    token: str = Depends(require_agent_token),
    db: Session = Depends(get_db),
):
    agent_code = _authorize_agent(payload.agent_code, token, db)
    payload.agent_code = agent_code
    with _agent_tenant(db, agent_code):
        try:
            return apply_payment_task_result(db, task_id, payload, agent_code)
        except PaymentCollectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/draft-tasks/claim")
def claim_draft_task_api(
    payload: AgentTaskClaim,
    token: str = Depends(require_agent_token),
    db: Session = Depends(get_db),
):
    code = _authorize_agent(payload.agent_code, token, db)
    with _agent_tenant(db, code):
        return {"ok": True, "task": claim_draft_task(db, code)}


@router.get("/draft-tasks/{task_id}/file")
def draft_task_file(
    task_id: int,
    agent_code: str,
    token: str = Depends(require_agent_token),
    db: Session = Depends(get_db),
):
    code = _authorize_agent(agent_code, token, db)
    with _agent_tenant(db, code):
        task = db.get(PaymentDraftTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="制单任务不存在")
        if task.agent_code and task.agent_code != code:
            raise HTTPException(status_code=403, detail="任务不属于该 Agent")
        path = Path(task.file_path or "")
        if not path.is_file():
            raise HTTPException(status_code=404, detail="制单文件不存在")
        return FileResponse(str(path), filename=path.name)


@router.post("/draft-tasks/{task_id}/result")
def report_draft_task_result(
    task_id: int,
    payload: AgentTaskResult,
    token: str = Depends(require_agent_token),
    db: Session = Depends(get_db),
):
    code = _authorize_agent(payload.agent_code, token, db)
    with _agent_tenant(db, code):
        try:
            return apply_draft_task_result(db, task_id, payload, code)
        except DraftError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _handle_diagnostic_upload(
    *,
    db: Session,
    task_kind: str,
    task_id: int,
    agent_code: str,
    attempt: int,
    kind: str,
    error_code: str,
    stage: str,
    file: UploadFile,
) -> dict:
    if task_kind == "collection":
        task = db.get(CollectionTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="采集任务不存在")
        if task.agent_code and task.agent_code != agent_code:
            raise HTTPException(status_code=403, detail="任务不属于该 Agent")
        tenant_id = task.tenant_id
    elif task_kind == "draft":
        task = db.get(PaymentDraftTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="制单任务不存在")
        if task.agent_code and task.agent_code != agent_code:
            raise HTTPException(status_code=403, detail="任务不属于该 Agent")
        tenant_id = task.tenant_id
    else:
        raise HTTPException(status_code=400, detail="不支持的任务类型")

    content = await file.read()
    try:
        row = save_diagnostic_file(
            db,
            task_kind=task_kind,
            task_id=task_id,
            attempt=attempt,
            filename=file.filename or "diagnostic.bin",
            content=content,
            content_type=file.content_type or "",
            kind=kind,
            error_code=error_code,
            stage=stage,
            tenant_id=tenant_id,
        )
    except DiagnosticError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {
        "ok": True,
        "id": row.id,
        "kind": row.kind,
        "size": row.size,
        "name": row.original_name,
    }


@router.post("/tasks/{task_id}/diagnostics")
async def upload_task_diagnostic(
    task_id: int,
    agent_code: str = Form(...),
    attempt: int = Form(1),
    task_kind: str = Form("collection"),
    kind: str = Form(""),
    error_code: str = Form(""),
    stage: str = Form(""),
    file: UploadFile = File(...),
    token: str = Depends(require_agent_token),
    db: Session = Depends(get_db),
):
    code = _authorize_agent(agent_code, token, db)
    with _agent_tenant(db, code):
        return await _handle_diagnostic_upload(
            db=db,
            task_kind=task_kind,
            task_id=task_id,
            agent_code=code,
            attempt=attempt,
            kind=kind,
            error_code=error_code,
            stage=stage,
            file=file,
        )


@router.post("/draft-tasks/{task_id}/diagnostics")
async def upload_draft_diagnostic(
    task_id: int,
    agent_code: str = Form(...),
    attempt: int = Form(1),
    kind: str = Form(""),
    error_code: str = Form(""),
    stage: str = Form(""),
    file: UploadFile = File(...),
    token: str = Depends(require_agent_token),
    db: Session = Depends(get_db),
):
    code = _authorize_agent(agent_code, token, db)
    with _agent_tenant(db, code):
        return await _handle_diagnostic_upload(
            db=db,
            task_kind="draft",
            task_id=task_id,
            agent_code=code,
            attempt=attempt,
            kind=kind,
            error_code=error_code,
            stage=stage,
            file=file,
        )


@router.get("/plugins/desired")
def get_desired_plugins(
    agent_code: str,
    token: str = Depends(require_agent_token),
    db: Session = Depends(get_db),
):
    code = _authorize_agent(agent_code, token, db)
    with _agent_tenant(db, code):
        agent = db.execute(select(Agent).where(Agent.code == code)).scalars().first()
        channel = (agent.channel if agent else "prod") or "prod"
        return {
            "ok": True,
            "agent_code": code,
            "channel": channel,
            "desired_plugins": desired_plugins_for_agent(db, code),
        }


@router.get("/plugins/{release_id}/download")
def download_plugin_release(
    release_id: int,
    agent_code: str,
    token: str = Depends(require_agent_token),
    db: Session = Depends(get_db),
):
    _authorize_agent(agent_code, token, db)
    row = db.get(PluginRelease, release_id)
    if row is None:
        raise HTTPException(status_code=404, detail="插件版本不存在")
    try:
        path = resolve_release_path(row)
    except PluginError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="插件文件已丢失")
    return FileResponse(str(path), filename=path.name, media_type="application/zip")
