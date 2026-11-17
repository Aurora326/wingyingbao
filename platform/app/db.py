from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Table, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.dml import Delete, Update
from sqlalchemy.sql.selectable import Alias, Select

from app.config import settings
from app.tenancy import current_tenant_id, default_tenant_id

engine = create_engine(settings.db_url, future=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def _collect_tables(source, tables: set[Table]) -> None:
    if isinstance(source, Table):
        tables.add(source)
        return
    if isinstance(source, Alias):
        return
    for child in getattr(source, "get_children", lambda: [])():
        _collect_tables(child, tables)


@event.listens_for(Session, "do_orm_execute")
def _apply_tenant_scope(execute_state) -> None:
    tenant_id = current_tenant_id()
    if tenant_id is None:
        return
    statement = execute_state.statement
    if isinstance(statement, Select):
        tables: set[Table] = set()
        for source in statement.get_final_froms():
            _collect_tables(source, tables)
        for table in tables:
            if "tenant_id" in table.c:
                statement = statement.where(table.c.tenant_id == tenant_id)
        execute_state.statement = statement
    elif isinstance(statement, (Update, Delete)):
        if "tenant_id" in statement.table.c:
            execute_state.statement = statement.where(statement.table.c.tenant_id == tenant_id)


@event.listens_for(Session, "before_flush")
def _assign_tenant_id(session, flush_context, instances) -> None:  # noqa: ANN001
    tenant_id = current_tenant_id()
    if tenant_id is None:
        tenant_id = default_tenant_id()
    if tenant_id is None:
        return
    for obj in session.new:
        if obj.__class__.__name__ in ("Tenant", "AppUser"):
            continue
        if hasattr(obj, "tenant_id") and getattr(obj, "tenant_id", None) is None:
            obj.tenant_id = tenant_id


def init_db() -> None:
    from alembic import command
    from alembic.config import Config

    alembic_config = Config(str(settings.base_dir / "alembic.ini"))
    command.upgrade(alembic_config, "head")


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
