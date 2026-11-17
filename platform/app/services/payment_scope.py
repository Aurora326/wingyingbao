"""出纳可见的账套范围：自己名下 + 别人委托给自己且仍在有效期内的。"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppUser, CashierDelegation, CashierScope
from app.services.permissions import effective_role

CASHIER_ROLE = "cashier"


def allowed_book_sets(db: Session, user) -> set[int] | None:
    """管理员和财务总监返回 None 表示不限；出纳返回授权账套集合（可能为空）。"""
    if user is None or effective_role(getattr(user, "role", "")) != CASHIER_ROLE:
        return None
    books = set(
        db.execute(
            select(CashierScope.book_set_id).where(
                CashierScope.user_id == user.id,
                CashierScope.role == CASHIER_ROLE,
                CashierScope.status == "active",
            )
        ).scalars().all()
    )
    today = dt.date.today()
    owners = db.execute(
        select(CashierDelegation.from_user_id).where(
            CashierDelegation.to_user_id == user.id,
            CashierDelegation.status == "active",
            CashierDelegation.start_date <= today,
            CashierDelegation.end_date >= today,
        )
    ).scalars().all()
    if not owners:
        return books
    delegated = db.execute(
        select(CashierScope.book_set_id).where(
            CashierScope.user_id.in_(owners),
            CashierScope.role == CASHIER_ROLE,
            CashierScope.status == "active",
        )
    ).scalars().all()
    return books | set(delegated)


def active_delegations(db: Session, user) -> list[tuple[str, dt.date]]:
    """委托给当前用户的出纳姓名和到期日，用于页面提示。"""
    if user is None:
        return []
    today = dt.date.today()
    rows = db.execute(
        select(AppUser.display_name, AppUser.username, CashierDelegation.end_date)
        .join(CashierDelegation, CashierDelegation.from_user_id == AppUser.id)
        .where(
            CashierDelegation.to_user_id == user.id,
            CashierDelegation.status == "active",
            CashierDelegation.start_date <= today,
            CashierDelegation.end_date >= today,
        )
        .order_by(CashierDelegation.end_date)
    ).all()
    return [((name or username), end) for name, username, end in rows]
