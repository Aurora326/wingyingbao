from __future__ import annotations

from contextlib import contextmanager

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Agent
from app.schemas import AgentDeviceReport, AgentTaskClaim, AgentTaskResult
from app.services.agent_report import AgentReportError, apply_device_report
from app.services.agent_tasks import (
    AgentTaskError,
    apply_agent_task_result,
    claim_agent_task,
    get_agent_task_credentials,
    get_agent_task_status,
)
from app.services.payment_collect import (
    PaymentCollectError,
    apply_payment_task_result,
    claim_payment_task,
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


def require_agent_token(
    authorization: str | None = Header(default=None),
    x_agent_token: str | None = Header(default=None),
) -> str:
    tokens = set(getattr(settings, "agent_tokens", set()))
    tokens.update(_token_scopes())
    if not tokens:
        raise HTTPException(status_code=503, detail="平台未配置 Agent 令牌")
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_agent_token:
        token = x_agent_token.strip()
    if token not in tokens:
        raise HTTPException(status_code=401, detail="Agent 令牌无效")
    return token


def _authorize_agent(agent_code: str, token: str) -> str:
    code = (agent_code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="缺少 agent_code")
    scopes = _token_scopes()
    if scopes:
        allowed = scopes.get(token, set())
        if code not in allowed:
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
    agent_code = _authorize_agent(payload.agent_code, token)
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
    agent_code = _authorize_agent(payload.agent_code, token)
    with _agent_tenant(db, agent_code):
        return {"ok": True, "task": claim_agent_task(db, agent_code)}


@router.get("/tasks/{task_id}/status")
def task_status(
    task_id: int,
    agent_code: str,
    token: str = Depends(require_agent_token),
    db: Session = Depends(get_db),
):
    code = _authorize_agent(agent_code, token)
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
    code = _authorize_agent(agent_code, token)
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
    agent_code = _authorize_agent(payload.agent_code, token)
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
    agent_code = _authorize_agent(payload.agent_code, token)
    with _agent_tenant(db, agent_code):
        return {"ok": True, "task": claim_payment_task(db, agent_code)}


@router.post("/payment-tasks/{task_id}/result")
def report_payment_task_result(
    task_id: int,
    payload: AgentTaskResult,
    token: str = Depends(require_agent_token),
    db: Session = Depends(get_db),
):
    agent_code = _authorize_agent(payload.agent_code, token)
    payload.agent_code = agent_code
    with _agent_tenant(db, agent_code):
        try:
            return apply_payment_task_result(db, task_id, payload, agent_code)
        except PaymentCollectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
