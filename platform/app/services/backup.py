"""PostgreSQL 备份、恢复与备份状态记录。

备份文件默认写到 data/backups/<时间戳>/，包含数据库 dump 和归档文件副本。
恢复会覆盖现有数据库对象，只应在确认备份可用后由管理员执行。
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
from pathlib import Path

from sqlalchemy.engine import make_url

from app.config import settings

DEFAULT_KEEP_DAYS = 14


def _pg_url(db_url: str | None = None) -> str:
    url = make_url(db_url or settings.db_url)
    if not url.drivername.startswith(("postgresql", "postgres")):
        raise RuntimeError("备份与恢复仅支持 PostgreSQL，请配置 PLATFORM_DB_URL")
    # pg_dump / pg_restore 不认识 SQLAlchemy 的 +psycopg 驱动后缀
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def _pg_env(db_url: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    password = make_url(db_url or settings.db_url).password
    if password:
        env["PGPASSWORD"] = password
    return env


def backup_postgres(target_dir: Path, db_url: str | None = None) -> Path:
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    target = target_dir / f"app_{stamp}.dump"
    try:
        result = subprocess.run(
            ["pg_dump", "--dbname", _pg_url(db_url), "--format=custom", "--no-owner", "--file", str(target)],
            env=_pg_env(db_url),
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("未找到 pg_dump，请安装 PostgreSQL 客户端（postgresql-client）") from exc
    if result.returncode != 0:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"pg_dump 失败: {result.stderr.strip()[:500]}")
    return target


def restore_postgres(dump_path: Path, db_url: str | None = None) -> None:
    dump_path = Path(dump_path)
    if not dump_path.exists():
        raise RuntimeError(f"备份文件不存在: {dump_path}")
    try:
        result = subprocess.run(
            ["pg_restore", "--clean", "--if-exists", "--no-owner", "--dbname", _pg_url(db_url), str(dump_path)],
            env=_pg_env(db_url),
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("未找到 pg_restore，请安装 PostgreSQL 客户端（postgresql-client）") from exc
    if result.returncode != 0:
        raise RuntimeError(f"pg_restore 失败: {result.stderr.strip()[:500]}")


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


def list_backups(root: Path | None = None) -> list[dict]:
    root = Path(root or settings.data_dir / "backups")
    if not root.exists():
        return []
    items = []
    for entry in sorted(root.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        size = sum(path.stat().st_size for path in entry.rglob("*") if path.is_file())
        items.append(
            {
                "name": entry.name,
                "size": size,
                "dumps": [path.name for path in sorted(entry.glob("*.dump"))],
                "created_at": dt.datetime.fromtimestamp(entry.stat().st_mtime),
            }
        )
    return items


def create_backup(
    artifacts_dir: Path | None = None,
    target_root: Path | None = None,
    keep_days: int = DEFAULT_KEEP_DAYS,
    master_key_path: Path | None = None,
) -> Path:
    artifacts_dir = Path(artifacts_dir or settings.artifacts_dir)
    target_root = Path(target_root or settings.data_dir / "backups")
    key_src = Path(master_key_path or settings.master_key_path)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    target = target_root / stamp
    target.mkdir(parents=True, exist_ok=True)
    backup_postgres(target)
    copy_new_files(artifacts_dir, target / "artifacts")
    if key_src.is_file():
        key_dst = target / "master.key"
        shutil.copy2(key_src, key_dst)
        try:
            os.chmod(key_dst, 0o600)
        except OSError:
            pass
    prune_backups(target_root, keep_days)
    return target


def run_backup_job(keep_days: int = DEFAULT_KEEP_DAYS) -> Path | None:
    """在后台线程里执行一次备份，并把结果写进平台设置供界面展示。"""
    from app.db import SessionLocal
    from app.services import app_settings

    with SessionLocal() as db:
        if app_settings.get_bool(db, "backup_running", False):
            return None
        app_settings.set_value(db, "backup_running", "1")
        app_settings.set_value(db, "backup_last_error", "")

    try:
        target = create_backup(keep_days=keep_days)
    except Exception as exc:  # noqa: BLE001
        with SessionLocal() as db:
            app_settings.set_value(db, "backup_running", "0")
            app_settings.set_value(db, "backup_last_error", str(exc)[:250])
        return None

    size = sum(path.stat().st_size for path in target.rglob("*") if path.is_file())
    with SessionLocal() as db:
        app_settings.set_value(db, "backup_running", "0")
        app_settings.set_value(db, "backup_last_at", dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
        app_settings.set_value(db, "backup_last_file", target.name)
        app_settings.set_value(db, "backup_last_size", str(size))
    return target
