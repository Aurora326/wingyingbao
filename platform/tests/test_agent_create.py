from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Agent, AppUser, Tenant
from app.services import security


def _user(db, username: str, role: str) -> tuple[Tenant, AppUser]:
    tenant = Tenant(name=f"租户-{username}", code=f"t-{username}")
    db.add(tenant)
    db.flush()
    user = AppUser(
        username=username,
        password_hash=security.hash_password("Passw0rd!"),
        role=role,
        tenant_id=tenant.id,
    )
    db.add(user)
    db.commit()
    return tenant, user


def test_create_agent_issues_token_and_registers(db):
    tenant, _ = _user(db, "agent-admin", "admin")
    with TestClient(app) as client:
        assert client.post(
            "/login", data={"username": "agent-admin", "password": "Passw0rd!"}, follow_redirects=False
        ).status_code == 303
        resp = client.post("/api/agents", json={"code": "warehouse-01", "name": "仓库采集电脑"})
        assert resp.status_code == 200, resp.text
        token = resp.json()["token"]
        assert token

        with SessionLocal() as check:
            agent = check.execute(select(Agent).where(Agent.code == "warehouse-01")).scalars().first()
            assert agent is not None
            assert agent.name == "仓库采集电脑"
            assert agent.tenant_id == tenant.id
            assert agent.token_hash and not agent.token_enc == token

        headers = {"X-Agent-Token": token}
        assert client.get("/api/agent/ping", headers=headers).status_code == 200
        claim = client.post(
            "/api/agent/payment-tasks/claim", json={"agent_code": "warehouse-01"}, headers=headers
        )
        assert claim.status_code == 200, claim.text
        assert claim.json()["task"] is None
        # 令牌只能操作自己这个编号
        other = client.post(
            "/api/agent/payment-tasks/claim", json={"agent_code": "local"}, headers=headers
        )
        assert other.status_code == 403
        # 编号不能重复
        dup = client.post("/api/agents", json={"code": "warehouse-01", "name": "重复"})
        assert dup.status_code == 409


def test_create_agent_requires_admin(db):
    _user(db, "agent-viewer", "viewer")
    with TestClient(app) as client:
        assert client.post(
            "/login", data={"username": "agent-viewer", "password": "Passw0rd!"}, follow_redirects=False
        ).status_code == 303
        resp = client.post("/api/agents", json={"code": "warehouse-02", "name": "无权限"})
        assert resp.status_code == 403
