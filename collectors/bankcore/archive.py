from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

KNOWN_SUFFIXES = {".xlsx", ".xls", ".csv", ".pdf", ".zip"}


def sniff_suffix(path: Path) -> str:
    try:
        with open(path, "rb") as handle:
            head = handle.read(8)
    except OSError:
        return ""
    if head.startswith(b"PK\x03\x04"):
        return ".xlsx"
    if head.startswith(b"%PDF"):
        return ".pdf"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return ".xls"
    return ""


def archive_file(
    source: Path,
    root: Path,
    prefix: str,
    date_tag: dt.date | str | None = None,
    account_suffix: str = "",
    archive_by_month: bool = True,
) -> Path:
    day = date_tag or dt.date.today()
    day_str = day.strftime("%Y%m%d") if isinstance(day, dt.date) else str(day)
    target_dir = Path(root)
    if archive_by_month:
        target_dir = target_dir / day.strftime("%Y") / day.strftime("%m")
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower()
    if suffix not in KNOWN_SUFFIXES:
        suffix = sniff_suffix(source) or suffix
    name = f"{prefix}_{day_str}{('_' + account_suffix) if account_suffix else ''}{suffix}"
    target = target_dir / name
    if target.exists():
        target.unlink()
    shutil.move(str(source), str(target))
    return target.resolve()


def cleanup_old_archives(root: Path, retention_months: int) -> int:
    if retention_months <= 0 or not root.exists():
        return 0
    cutoff = dt.date.today() - dt.timedelta(days=retention_months * 31)
    removed = 0
    for month_dir in root.glob("*/*"):
        if not month_dir.is_dir():
            continue
        try:
            month_date = dt.date(int(month_dir.parent.name), int(month_dir.name), 1)
        except (ValueError, TypeError):
            continue
        if month_date < cutoff.replace(day=1):
            shutil.rmtree(month_dir, ignore_errors=True)
            removed += 1
    return removed
