from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    Agent,
    AppUser,
    Artifact,
    AuditLog,
    BalanceSnapshot,
    BankAccount,
    BookSet,
    CashierScope,
    CollectionTask,
    Credential,
    Enterprise,
    InternalDoc,
    PayeeAccount,
    PaymentCollectTask,
    PaymentSourceAccount,
    PayeeAccountChange,
    PaymentDoc,
    PaymentDocChange,
    PaymentReview,
    SubAccount,
    Receipt,
    ReconBatch,
    ReconItem,
    Statement,
    TaskRun,
    Tenant,
    UkeyDevice,
)
from app.services import agents, security
from app.tenancy import invalidate_default_tenant

TENANT_TABLES = (
    Agent,
    UkeyDevice,
    BankAccount,
    Credential,
    CollectionTask,
    TaskRun,
    Artifact,
    Statement,
    Receipt,
    BalanceSnapshot,
    InternalDoc,
    ReconBatch,
    ReconItem,
    AuditLog,
    Enterprise,
    BookSet,
    CashierScope,
    PayeeAccount,
    PaymentCollectTask,
    PaymentSourceAccount,
    PayeeAccountChange,
    PaymentDoc,
    PaymentDocChange,
    PaymentReview,
    SubAccount,
)


def seed(db: Session) -> None:
    tenant = db.execute(select(Tenant).order_by(Tenant.id)).scalars().first()
    if tenant is None:
        tenant = Tenant(name="默认租户", code="default")
        db.add(tenant)
        db.commit()
        invalidate_default_tenant()

    agents.ensure_local_agent(db)

    user = db.execute(select(AppUser).where(AppUser.username == settings.admin_user)).scalars().first()
    if user is None:
        db.add(
            AppUser(
                username=settings.admin_user,
                password_hash=security.hash_password(settings.admin_password),
                role="admin",
            )
        )

    enterprise = db.execute(select(Enterprise).order_by(Enterprise.id)).scalars().first()
    if enterprise is None:
        enterprise = Enterprise(name="默认企业", tenant_id=tenant.id)
        db.add(enterprise)
        db.flush()

    if settings.enable_mock_bank:
        demo = db.execute(select(BankAccount).where(BankAccount.bank_code == "mock")).scalars().first()
        if demo is None:
            demo = BankAccount(
                enterprise_id=enterprise.id,
                bank_code="mock",
                account_no="MOCK0001",
                account_name="演示账户",
                collect_enabled=False,
                collect_cron="0 3 * * *",
                days_back=30,
                tenant_id=tenant.id,
            )
            db.add(demo)
            db.flush()
            db.add(
                Credential(
                    bank_account_id=demo.id,
                    username_enc=security.encrypt("demo"),
                    password_enc=security.encrypt("demo"),
                    pin_enc=security.encrypt("demo"),
                    tenant_id=tenant.id,
                )
            )

    for model in TENANT_TABLES:
        db.execute(
            update(model)
            .where(model.tenant_id.is_(None))
            .values(tenant_id=tenant.id)
        )
    # 付款方名称即账套名称，历史账套名统一回填
    for book in db.execute(select(BookSet)).scalars().all():
        enterprise = db.get(Enterprise, book.enterprise_id)
        if enterprise is not None and book.name != enterprise.name:
            book.name = enterprise.name
    db.commit()
