from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Table, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.dml import Delete, Update
from sqlalchemy.sql.selectable import Alias, Select

from app.config import settings
from app.models import Base
from app.tenancy import current_tenant_id, default_tenant_id

connect_args = {"check_same_thread": False} if settings.db_url.startswith("sqlite") else {}

engine = create_engine(settings.db_url, connect_args=connect_args, future=True)

if settings.db_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


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


COLUMN_ADDITIONS = {
    "ukey_device": {
        "backend": "VARCHAR(16) NOT NULL DEFAULT 'gf3'",
        "external_id": "VARCHAR(64) NOT NULL DEFAULT ''",
        "agent_code": "VARCHAR(32) NOT NULL DEFAULT 'local'",
        "extra": "JSON",
        "tenant_id": "INTEGER",
    },
    "bank_account": {
        "collect_mode": "VARCHAR(16) NOT NULL DEFAULT 'both'",
        "account_type": "VARCHAR(16) NOT NULL DEFAULT ''",
        "payment_enabled": "BOOLEAN NOT NULL DEFAULT 1",
        "auto_voucher_enabled": "BOOLEAN NOT NULL DEFAULT 1",
        "has_ukey": "BOOLEAN NOT NULL DEFAULT 1",
        "usage_tag": "VARCHAR(64) NOT NULL DEFAULT ''",
        "account_status": "VARCHAR(16) NOT NULL DEFAULT '正常'",
        "parent_account_id": "INTEGER",
        "has_sub_accounts": "BOOLEAN NOT NULL DEFAULT 0",
    },
    "payment_doc": {
        "payer_sub_account_id": "INTEGER",
        "payment_status": "VARCHAR(16) NOT NULL DEFAULT ''",
        "approval_node": "VARCHAR(128) NOT NULL DEFAULT ''",
        "approval_handler": "VARCHAR(128) NOT NULL DEFAULT ''",
    },
    "payment_collect_task": {
        "agent_code": "VARCHAR(32) NOT NULL DEFAULT ''",
        "limit_nocontract": "INTEGER NOT NULL DEFAULT -1",
        "lookback_days": "INTEGER NOT NULL DEFAULT 0",
        "source": "VARCHAR(16) NOT NULL DEFAULT 'mingyuan'",
    },
    "payment_source_account": {
        "collect_enabled": "BOOLEAN NOT NULL DEFAULT 0",
        "collect_cron": "VARCHAR(64) NOT NULL DEFAULT '0 8 * * *'",
        "collect_limit": "INTEGER NOT NULL DEFAULT 200",
        "collect_nocontract": "INTEGER NOT NULL DEFAULT 100",
        "collect_days": "INTEGER NOT NULL DEFAULT 30",
    },
    "collection_task": {
        "agent_code": "VARCHAR(32) NOT NULL DEFAULT 'local'",
        "cancel_requested": "BOOLEAN NOT NULL DEFAULT 0",
        "cancel_requested_at": "DATETIME",
        "tenant_id": "INTEGER",
    },
}
for _table in (
    "agent",
    "enterprise",
    "bank_account",
    "credential",
    "task_run",
    "artifact",
    "statement",
    "receipt",
    "balance_snapshot",
    "internal_doc",
    "recon_batch",
    "recon_item",
    "audit_log",
    "app_user",
):
    COLUMN_ADDITIONS.setdefault(_table, {})["tenant_id"] = "INTEGER"


def ensure_schema(target_engine=None) -> None:
    target = target_engine or engine
    if not str(target.url).startswith("sqlite"):
        return
    inspector = inspect(target)
    tables = set(inspector.get_table_names())
    with target.begin() as connection:
        for table, additions in COLUMN_ADDITIONS.items():
            if table not in tables:
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, ddl in additions.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                if name == "tenant_id":
                    connection.execute(
                        text(f"CREATE INDEX IF NOT EXISTS ix_{table}_tenant_id ON {table} (tenant_id)")
                    )


def init_db() -> None:
    from alembic import command
    from alembic.config import Config

    alembic_config = Config(str(settings.base_dir / "alembic.ini"))
    tables = set(inspect(engine).get_table_names())
    if tables and "alembic_version" not in tables:
        # 历史库没有版本表：先把旧补列对齐到当前模型，再标记为基线
        ensure_schema()
        command.stamp(alembic_config, "head")
    else:
        command.upgrade(alembic_config, "head")


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
