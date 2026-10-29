from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sqlite3
import sys
from pathlib import Path

from app.config import settings


def backup_database(db_path: Path, target_dir: Path) -> Path:
    db_path = Path(db_path)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / db_path.name
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        destination = sqlite3.connect(target)
        try:
            with destination:
                source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
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
    db_path: Path | None = None,
    artifacts_dir: Path | None = None,
    target_root: Path | None = None,
    keep_days: int = 14,
) -> Path:
    db_path = Path(db_path or settings.data_dir / "app.db")
    artifacts_dir = Path(artifacts_dir or settings.artifacts_dir)
    target_root = Path(target_root or settings.data_dir / "backups")
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    target = target_root / stamp
    target.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        backup_database(db_path, target)
    copied = copy_new_files(artifacts_dir, target / "artifacts")
    prune_backups(target_root, keep_days)
    print(f"备份完成: {target}（新增文件 {copied} 个）")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="备份 SQLite 数据库与归档文件")
    parser.add_argument("--target", default="", help="备份根目录，默认 data/backups")
    parser.add_argument("--keep-days", type=int, default=14, help="备份保留天数")
    args = parser.parse_args()
    create_backup(target_root=Path(args.target) if args.target else None, keep_days=args.keep_days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
