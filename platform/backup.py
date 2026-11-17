"""备份与恢复 PostgreSQL（命令行入口）。

用法：
    python backup.py                          # 立即备份，默认写到 data/backups
    python backup.py --list                   # 列出已有备份
    python backup.py --restore app_xxx.dump   # 从 dump 恢复（会覆盖现有数据库对象）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.services import backup as service


def main() -> int:
    parser = argparse.ArgumentParser(description="备份与恢复 PostgreSQL 数据库")
    parser.add_argument("--target", default="", help="备份根目录，默认 data/backups")
    parser.add_argument("--keep-days", type=int, default=service.DEFAULT_KEEP_DAYS, help="备份保留天数")
    parser.add_argument("--list", action="store_true", help="列出已有备份")
    parser.add_argument("--restore", default="", metavar="DUMP", help="从 .dump 文件恢复数据库")
    args = parser.parse_args()

    if args.list:
        items = service.list_backups(Path(args.target) if args.target else None)
        if not items:
            print("还没有备份")
            return 0
        for item in items:
            print(f"{item['created_at']:%Y-%m-%d %H:%M}  {item['size'] / 1024 / 1024:8.1f} MB  {item['name']}")
        return 0

    if args.restore:
        service.restore_postgres(Path(args.restore))
        print(f"恢复完成: {args.restore}")
        return 0

    target = service.create_backup(
        target_root=Path(args.target) if args.target else None,
        keep_days=args.keep_days,
    )
    print(f"备份完成: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
