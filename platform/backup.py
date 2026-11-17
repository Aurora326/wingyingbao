"""备份 PostgreSQL 数据库与归档文件。

需要本机可执行 pg_dump（版本不低于数据库服务端）。

用法：
    python backup.py [--target 备份根目录] [--keep-days 14]
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

from app.config import settings


def backup_postgres(target_dir: Path) -> Path:
    if not settings.db_url.startswith(("postgresql", "postgres")):
        raise RuntimeError("备份仅支持 PostgreSQL，请配置 PLATFORM_DB_URL")
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    target = target_dir / f"app_{stamp}.dump"
    url = make_url(settings.db_url)
    env = os.environ.copy()
    if url.password:
        env["PGPASSWORD"] = url.password
    # pg_dump 不认识 SQLAlchemy 的 +psycopg 驱动后缀
    pg_url = url.set(drivername="postgresql").render_as_string(hide_password=False)
    try:
        result = subprocess.run(
            ["pg_dump", "--dbname", pg_url, "--format=custom", "--no-owner", "--file", str(target)],
            env=env,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("未找到 pg_dump，请安装 PostgreSQL 客户端（postgresql-client）") from exc
    if result.returncode != 0:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"pg_dump 失败: {result.stderr.strip()}")
    return target


def copy_new_files(source: Path, target: Path) -> int:
    source = Path(source)
    target = Path(target)
    if not source.exists():
        return 0
    copied = 0
    for item in source.rglob("*"):
        if not item.is_file():
            continue
        relative = item.relative_to(source)
        destination = target / relative
        if destination.exists() and destination.stat().st_size == item.stat().st_size:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, destination)
        copied += 1
    return copied


def prune_backups(root: Path, keep_days: int) -> int:
    root = Path(root)
    if keep_days <= 0 or not root.exists():
        return 0
    cutoff = dt.datetime.now() - dt.timedelta(days=keep_days)
    removed = 0
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        if dt.datetime.fromtimestamp(entry.stat().st_mtime) < cutoff:
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
    return removed


def create_backup(
    artifacts_dir: Path | None = None,
    target_root: Path | None = None,
    keep_days: int = 14,
) -> Path:
    artifacts_dir = Path(artifacts_dir or settings.artifacts_dir)
    target_root = Path(target_root or settings.data_dir / "backups")
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    target = target_root / stamp
    target.mkdir(parents=True, exist_ok=True)
    dump = backup_postgres(target)
    print(f"数据库备份: {dump}")
    copied = copy_new_files(artifacts_dir, target / "artifacts")
    prune_backups(target_root, keep_days)
    print(f"备份完成: {target}（新增文件 {copied} 个）")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="备份 PostgreSQL 数据库与归档文件")
    parser.add_argument("--target", default="", help="备份根目录，默认 data/backups")
    parser.add_argument("--keep-days", type=int, default=14, help="备份保留天数")
    args = parser.parse_args()
    create_backup(target_root=Path(args.target) if args.target else None, keep_days=args.keep_days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
