from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Base

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


COLUMN_ADDITIONS = {
    "ukey_device": {
        "backend": "VARCHAR(16) NOT NULL DEFAULT 'gf3'",
        "external_id": "VARCHAR(64) NOT NULL DEFAULT ''",
        "agent_code": "VARCHAR(32) NOT NULL DEFAULT 'local'",
        "extra": "JSON",
    },
    "collection_task": {
        "agent_code": "VARCHAR(32) NOT NULL DEFAULT 'local'",
    },
}


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


def init_db() -> None:
    Base.metadata.create_all(engine)
    ensure_schema()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
