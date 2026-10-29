from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import agent as agent_api
from app.main import app
from app.models import BankAccount, CollectionTask, Credential, Enterprise, TaskRun, UkeyDevice
from app.schemas import AgentTaskResult
from app.services import security
from app.services.agent_tasks import (
    AgentTaskError,
    apply_agent_task_result,
    claim_agent_task,
    get_agent_task_credentials,
)
from app.services.scheduler import enqueue_task


def _seed(db, agent_code: str = "cust-1517", account_no: str = "6222000011112222") -> CollectionTask:
    enterprise = Enterprise(name=f"测试企业-{account_no}")
    db.add(enterprise)
    db.flush()
    device = UkeyDevice(
        name="工商银行 · 1号口",
        bank_code="icbc",
        backend="mb",
        agent_code=agent_code,
        gf3_cabinet=1547,
        gf3_port=1,
        status="online",
        extra={"ter_id": "068330001547"},
    )
    db.add(device)
    db.flush()
    account = BankAccount(
        enterprise_id=enterprise.id,
        bank_code="icbc",
        account_no=account_no,
        account_name="测试账户",
        ukey_device_id=device.id,
        days_back=30,
    )
    db.add(account)
    db.flush()
    db.add(
        Credential(
            bank_account_id=account.id,
            username_enc=security.encrypt("user"),
            password_enc=security.encrypt("pass"),
            pin_enc=security.encrypt("123456"),
        )
    )
    db.commit()
    return enqueue_task(
        db,
        account,
        "statements",
        dt.date(2026, 10, 1),
        dt.date(2026, 10, 29),
        trigger="manual",
        created_by="test",
    )


def test_claim_only_returns_own_tasks(db):
    task = _seed(db, account_no="6222000011112223")
    assert claim_agent_task(db, "other-agent") is None
    payload = claim_agent_task(db, "cust-1517")
    assert payload is not None
    assert payload["task_id"] == task.id
    assert payload["attempt"] == 1
    assert payload["bank_code"] == "icbc"
    assert payload["ter_id"] == "068330001547"
    assert "credentials" not in payload

    credentials = get_agent_task_credentials(db, task.id, "cust-1517", 1)
    assert credentials["username"] == "user"
    assert credentials["password"] == "pass"
    assert credentials["pin"] == "123456"

    db.expire_all()
    row = db.get(CollectionTask, task.id)
    assert row.status == "leased"
    assert row.attempts == 1
    assert row.lease_until is not None


def test_stale_lease_requeues_and_can_be_reclaimed(db):
    task = _seed(db, account_no="6222000011112224")
    claim_agent_task(db, "cust-1517")
    db.expire_all()
    row = db.get(CollectionTask, task.id)
    row.lease_until = dt.datetime.now() - dt.timedelta(minutes=1)
    db.commit()

    payload = claim_agent_task(db, "cust-1517")
    assert payload is not None
    assert payload["attempt"] == 2
    db.expire_all()
    assert db.get(CollectionTask, task.id).status == "leased"


def test_apply_result_finishes_task(db):
    task = _seed(db, account_no="6222000011112225")
    claim_agent_task(db, "cust-1517")
    result = apply_agent_task_result(
        db,
        task.id,
        AgentTaskResult(
            agent_code="cust-1517",
            attempt=1,
            exit_code=0,
            duration_ms=1200,
            log_text="collector ok",
            result={"no_data": True, "error_code": "", "message": ""},
        ),
    )
    assert result["status"] == "succeeded"
    assert result["error_code"] == "NO_DATA"

    db.expire_all()
    row = db.get(CollectionTask, task.id)
    assert row.status == "succeeded"
    assert row.finished_at is not None
    run = db.execute(select(TaskRun).where(TaskRun.task_id == task.id)).scalars().first()
    assert run is not None
    assert run.agent_id == "cust-1517"
    assert run.exit_code == 0


def test_apply_result_rejects_wrong_agent_and_attempt(db):
    task = _seed(db, account_no="6222000011112226")
    claim_agent_task(db, "cust-1517")
    with pytest.raises(AgentTaskError) as wrong_agent:
        apply_agent_task_result(
            db,
            task.id,
            AgentTaskResult(agent_code="other-agent", attempt=1),
        )
    assert wrong_agent.value.status_code == 403

    with pytest.raises(AgentTaskError) as credentials_denied:
        get_agent_task_credentials(db, task.id, "other-agent")
    assert credentials_denied.value.status_code == 403

    with pytest.raises(AgentTaskError) as credentials_stale:
        get_agent_task_credentials(db, task.id, "cust-1517", 99)
    assert credentials_stale.value.status_code == 409

    with pytest.raises(AgentTaskError) as wrong_attempt:
        apply_agent_task_result(
            db,
            task.id,
            AgentTaskResult(agent_code="cust-1517", attempt=9),
        )
    assert wrong_attempt.value.status_code == 409


def test_claim_api_scopes_token_to_agent(db, monkeypatch):
    _seed(db, account_no="6222000011112227")
    monkeypatch.setattr(
        agent_api,
        "settings",
        SimpleNamespace(
            agent_tokens={"token-a"},
            agent_token_scopes={"token-a": {"cust-1517"}},
        ),
    )
    with TestClient(app) as client:
        denied = client.post(
            "/api/agent/tasks/claim",
            json={"agent_code": "other-agent"},
            headers={"Authorization": "Bearer token-a"},
        )
        assert denied.status_code == 403

        allowed = client.post(
            "/api/agent/tasks/claim",
            json={"agent_code": "cust-1517"},
            headers={"Authorization": "Bearer token-a"},
        )
        assert allowed.status_code == 200
        assert allowed.json()["task"]["task_id"] > 0
