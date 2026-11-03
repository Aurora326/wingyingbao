from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.main import app
from app.models import AppUser, BankAccount, Enterprise, Tenant
from app.services import security
from app.tenancy import tenant_context


def _tenant(db, code: str) -> tuple[Tenant, Enterprise]:
    tenant = Tenant(name=f"租户{code}", code=code)
    db.add(tenant)
    db.flush()
    enterprise = Enterprise(name=f"企业{code}", tenant_id=tenant.id)
    db.add(enterprise)
    db.commit()
    return tenant, enterprise


def test_tenant_scope_filters_and_writes(db):
    t1, e1 = _tenant(db, "scope-a")
    t2, e2 = _tenant(db, "scope-b")
    a1 = BankAccount(
        enterprise_id=e1.id, bank_code="icbc", account_no="SCOPE-A-1", tenant_id=t1.id
    )
    a2 = BankAccount(
        enterprise_id=e2.id, bank_code="icbc", account_no="SCOPE-B-1", tenant_id=t2.id
    )
    db.add_all([a1, a2])
    db.commit()

    with SessionLocal() as scoped:
        with tenant_context(t1.id):
            visible = scoped.execute(select(BankAccount)).scalars().all()
            assert [row.account_no for row in visible] == ["SCOPE-A-1"]
            assert scoped.execute(select(func.count(BankAccount.id))).scalar_one() == 1
            assert scoped.get(BankAccount, a2.id) is None
            assert scoped.execute(
                select(BankAccount).where(BankAccount.id == a2.id)
            ).scalars().first() is None
            created = BankAccount(
                enterprise_id=e1.id, bank_code="citic", account_no="SCOPE-A-2"
            )
            scoped.add(created)
            scoped.commit()
            assert created.tenant_id == t1.id

    with SessionLocal() as super_session:
        assert super_session.execute(select(func.count(BankAccount.id))).scalar_one() >= 3


def test_tenant_login_isolates_api(db):
    t1, e1 = _tenant(db, "login-a")
    t2, e2 = _tenant(db, "login-b")
    db.add_all(
        [
            BankAccount(enterprise_id=e1.id, bank_code="icbc", account_no="LOGIN-A-1", tenant_id=t1.id),
            BankAccount(enterprise_id=e2.id, bank_code="icbc", account_no="LOGIN-B-1", tenant_id=t2.id),
            AppUser(
                username="tenant-login-a",
                password_hash=security.hash_password("Passw0rd!"),
                role="admin",
                tenant_id=t1.id,
            ),
        ]
    )
    db.commit()

    with TestClient(app) as client:
        response = client.post(
            "/login",
            data={"username": "tenant-login-a", "password": "Passw0rd!"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        accounts = client.get("/api/accounts").json()["items"]
        assert [row["account_no"] for row in accounts] == ["LOGIN-A-1"]
        assert client.post("/api/accounts/999999/toggle").status_code == 404
