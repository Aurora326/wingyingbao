from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AppUser, BankAccount, Credential, Enterprise
from app.services import agents, security


def seed(db: Session) -> None:
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
        enterprise = Enterprise(name="默认企业")
        db.add(enterprise)
        db.flush()

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
        )
        db.add(demo)
        db.flush()
        db.add(
            Credential(
                bank_account_id=demo.id,
                username_enc=security.encrypt("demo"),
                password_enc=security.encrypt("demo"),
                pin_enc=security.encrypt("demo"),
            )
        )
    db.commit()
