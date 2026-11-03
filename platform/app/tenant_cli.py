from __future__ import annotations

import argparse

from sqlalchemy import func, select, update

from app.db import SessionLocal, init_db
from app.models import Agent, AppUser, BankAccount, CollectionTask, Enterprise, Tenant, UkeyDevice
from app.services import security
from app.tenancy import invalidate_default_tenant


def _tenant(db, value: str) -> Tenant:
    tenant = None
    if value.isdigit():
        tenant = db.get(Tenant, int(value))
    if tenant is None:
        tenant = db.execute(select(Tenant).where(Tenant.code == value)).scalars().first()
    if tenant is None:
        raise SystemExit(f"租户不存在: {value}")
    return tenant


def cmd_list(_args) -> None:
    with SessionLocal() as db:
        for tenant in db.execute(select(Tenant).order_by(Tenant.id)).scalars():
            accounts = db.execute(
                select(func.count(BankAccount.id)).where(BankAccount.tenant_id == tenant.id)
            ).scalar_one()
            devices = db.execute(
                select(func.count(UkeyDevice.id)).where(UkeyDevice.tenant_id == tenant.id)
            ).scalar_one()
            tasks = db.execute(
                select(func.count(CollectionTask.id)).where(CollectionTask.tenant_id == tenant.id)
            ).scalar_one()
            users = db.execute(
                select(func.count(AppUser.id)).where(AppUser.tenant_id == tenant.id)
            ).scalar_one()
            print(
                f"{tenant.id}\t{tenant.code}\t{tenant.name}\t"
                f"账户 {accounts}\t设备 {devices}\t任务 {tasks}\t用户 {users}"
            )


def cmd_create(args) -> None:
    with SessionLocal() as db:
        exists = db.execute(select(Tenant).where(Tenant.code == args.code)).scalars().first()
        if exists:
            raise SystemExit(f"租户编码已存在: {args.code}")
        tenant = Tenant(name=args.name, code=args.code)
        db.add(tenant)
        db.flush()
        db.add(Enterprise(name=args.name, tenant_id=tenant.id))
        db.commit()
        invalidate_default_tenant()
        print(f"已创建租户 {tenant.id} {tenant.code} {tenant.name}")


def cmd_user(args) -> None:
    with SessionLocal() as db:
        tenant = _tenant(db, args.tenant)
        exists = db.execute(select(AppUser).where(AppUser.username == args.username)).scalars().first()
        if exists:
            raise SystemExit(f"用户名已存在: {args.username}")
        db.add(
            AppUser(
                username=args.username,
                password_hash=security.hash_password(args.password),
                role=args.role,
                tenant_id=tenant.id,
            )
        )
        db.commit()
        print(f"已创建用户 {args.username}，归属租户 {tenant.code}")


def cmd_agent(args) -> None:
    with SessionLocal() as db:
        tenant = _tenant(db, args.tenant)
        agent = db.execute(select(Agent).where(Agent.code == args.agent_code)).scalars().first()
        if agent is None:
            agent = Agent(code=args.agent_code, name=args.agent_code, tenant_id=tenant.id)
            db.add(agent)
        else:
            agent.tenant_id = tenant.id
        db.execute(
            update(UkeyDevice)
            .where(UkeyDevice.agent_code == args.agent_code)
            .values(tenant_id=tenant.id)
        )
        db.commit()
        print(f"Agent {args.agent_code} 及名下设备已归属租户 {tenant.code}")


def main() -> None:
    parser = argparse.ArgumentParser(description="租户与账号管理")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="列出所有租户").set_defaults(func=cmd_list)

    create = sub.add_parser("create", help="创建租户")
    create.add_argument("name")
    create.add_argument("--code", required=True)
    create.set_defaults(func=cmd_create)

    user = sub.add_parser("user", help="为租户创建登录账号")
    user.add_argument("tenant", help="租户编码或 id")
    user.add_argument("username")
    user.add_argument("password")
    user.add_argument("--role", default="admin", choices=("admin", "operator", "viewer"))
    user.set_defaults(func=cmd_user)

    agent = sub.add_parser("agent", help="把 Agent 归属到租户")
    agent.add_argument("tenant", help="租户编码或 id")
    agent.add_argument("agent_code")
    agent.set_defaults(func=cmd_agent)

    args = parser.parse_args()
    init_db()
    args.func(args)


if __name__ == "__main__":
    main()
