from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

_current_tenant: ContextVar[int | None] = ContextVar("current_tenant_id", default=None)
_UNSET = object()
_default_tenant: int | None | object = _UNSET


def current_tenant_id() -> int | None:
    return _current_tenant.get()


def set_current_tenant(tenant_id: int | None):
    return _current_tenant.set(tenant_id)


def reset_current_tenant(token) -> None:
    _current_tenant.reset(token)


@contextmanager
def tenant_context(tenant_id: int | None):
    token = set_current_tenant(tenant_id)
    try:
        yield
    finally:
        reset_current_tenant(token)


def default_tenant_id() -> int | None:
    global _default_tenant
    if _default_tenant is not _UNSET:
        return _default_tenant  # type: ignore[return-value]
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Tenant

    with SessionLocal() as db:
        row = db.execute(select(Tenant).order_by(Tenant.id)).scalars().first()
        _default_tenant = row.id if row else None
    return _default_tenant  # type: ignore[return-value]


def invalidate_default_tenant() -> None:
    global _default_tenant
    _default_tenant = _UNSET
