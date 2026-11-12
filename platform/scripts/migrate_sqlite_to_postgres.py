"""把现有 SQLite 数据搬到 PostgreSQL。

用法（在 platform 目录下）：
    python scripts/migrate_sqlite_to_postgres.py \
        --source data/app.db \
        --target postgresql+psycopg://platform:platform@127.0.0.1:5432/platform

默认要求目标库所有业务表为空；确认可以清空时加 --truncate。
加 --dry-run 只统计行数，不写任何数据。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import MetaData, Table, create_engine, func, inspect, select, text

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

INT32_MIN, INT32_MAX = -(2**31), 2**31 - 1


def _coerce(value, column):
    """SQLite 读出的 0/1、字符串日期/JSON 按目标列类型转换。"""
    if value is None:
        return None
    try:
        target_type = column.type.python_type
    except NotImplementedError:
        return value
    if target_type is bool:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if target_type is int and isinstance(value, int):
        # Windows 的 -1 退出码在 SQLite 里以无符号 32 位保存为 4294967295，
        # PostgreSQL 的 INTEGER 只收有符号 32 位，这里折回去
        while value > INT32_MAX:
            value -= 2**32
        while value < INT32_MIN:
            value += 2**32
        return value
    if target_type is dict:
        if isinstance(value, (str, bytes)):
            raw = value.decode() if isinstance(value, bytes) else value
            return json.loads(raw) if raw.strip() else {}
        return value
    if target_type is datetime and isinstance(value, str):
        return datetime.fromisoformat(value)
    if target_type is date and isinstance(value, str):
        return date.fromisoformat(value[:10])
    if target_type is Decimal and isinstance(value, str):
        return Decimal(value)
    return value


def _order_self_references(table, payload):
    """自引用表（如 bank_account.parent_account_id）必须先插父行。"""
    self_fks = [fk.parent.name for fk in table.foreign_keys if fk.column.table is table]
    if not self_fks or "id" not in table.columns or not payload:
        return payload
    by_id = {row["id"]: row for row in payload}
    ordered, placed = [], set()
    pending = list(payload)
    while pending:
        next_pending = []
        for row in pending:
            parents = [row.get(name) for name in self_fks]
            if all(parent is None or parent not in by_id or parent in placed for parent in parents):
                ordered.append(row)
                placed.add(row["id"])
            else:
                next_pending.append(row)
        if len(next_pending) == len(pending):
            # 循环引用无法排序时保持原样，交由数据库报错
            ordered.extend(next_pending)
            break
        pending = next_pending
    return ordered


def _reset_sequences(connection, tables) -> None:
    if connection.dialect.name != "postgresql":
        return
    for table in tables:
        primary_keys = list(table.primary_key.columns)
        if len(primary_keys) != 1 or primary_keys[0].name != "id":
            continue
        name = table.name
        connection.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {name}), 1), "
                f"(SELECT MAX(id) IS NOT NULL FROM {name}))"
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite → PostgreSQL 数据迁移")
    parser.add_argument("--source", default="", help="SQLite 文件，默认 data/app.db")
    parser.add_argument("--target", default="", help="目标库 URL，默认 PLATFORM_DB_URL")
    parser.add_argument("--truncate", action="store_true", help="先清空目标库所有业务表")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写入")
    args = parser.parse_args()

    target_url = args.target or os.getenv("PLATFORM_DB_URL", "")
    if not target_url.startswith(("postgresql", "postgres")):
        print("目标必须是 PostgreSQL，例如 postgresql+psycopg://user:pass@host:5432/db", file=sys.stderr)
        return 2
    os.environ["PLATFORM_DB_URL"] = target_url

    from app.config import settings
    from app.models import Base

    source_path = Path(args.source) if args.source else settings.data_dir / "app.db"
    if not source_path.exists():
        print(f"源 SQLite 不存在: {source_path}", file=sys.stderr)
        return 2

    source_engine = create_engine(f"sqlite:///{source_path.as_posix()}")
    source_metadata = MetaData()
    source_metadata.reflect(bind=source_engine)

    plan = []
    for table in Base.metadata.sorted_tables:
        if table.name not in source_metadata.tables:
            print(f"跳过（源库无此表）: {table.name}")
            continue
        plan.append((source_metadata.tables[table.name], table))

    if args.dry_run:
        with source_engine.connect() as connection:
            total = 0
            for source_table, target_table in plan:
                count = connection.execute(select(func.count()).select_from(source_table)).scalar() or 0
                total += count
                print(f"{target_table.name}: {count}")
            print(f"合计 {total} 行")
        return 0

    from app.db import engine as target_engine, init_db

    # 目标库结构由 Alembic 负责，先升到最新版本
    init_db()

    with source_engine.connect() as source_conn, target_engine.begin() as target_conn:
        if args.truncate:
            names = ", ".join(target_table.name for _, target_table in reversed(plan))
            target_conn.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
        else:
            non_empty = [
                target_table.name
                for _, target_table in plan
                if target_conn.execute(select(func.count()).select_from(target_table)).scalar()
            ]
            if non_empty:
                print(f"目标库非空: {', '.join(non_empty)}；确认清空请加 --truncate", file=sys.stderr)
                return 1

        total = 0
        for source_table, target_table in plan:
            common = [name for name in source_table.columns.keys() if name in target_table.columns]
            rows = source_conn.execute(select(*(source_table.c[name] for name in common))).mappings().all()
            payload = [
                {key: _coerce(value, target_table.columns[key]) for key, value in row.items()}
                for row in rows
            ]
            payload = _order_self_references(target_table, payload)
            if payload:
                target_conn.execute(target_table.insert(), payload)
            total += len(payload)
            print(f"{target_table.name}: {len(payload)}")

        _reset_sequences(target_conn, [target_table for _, target_table in plan])
        print(f"迁移完成，共 {total} 行")

    return 0


if __name__ == "__main__":
    sys.exit(main())
