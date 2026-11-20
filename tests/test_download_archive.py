from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from common.download_helper import (
    cleanup_old_archives,
    ensure_archive_dir,
    resolve_browser_download_dir,
)


def test_resolve_browser_download_dir_dev(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    base = tmp_path / "ccb"
    target = resolve_browser_download_dir(base)
    assert target == base / "downloads"
    assert target.is_dir()


def test_resolve_browser_download_dir_frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app.exe"))
    target = resolve_browser_download_dir(tmp_path / "ignored")
    assert target == tmp_path / "downloads"
    assert target.is_dir()


def test_ensure_archive_dir_by_month_and_flat(tmp_path):
    now = dt.datetime(2026, 11, 20)
    monthly = ensure_archive_dir(tmp_path, True, now)
    assert monthly == tmp_path / "2026" / "11"
    assert monthly.is_dir()

    flat = ensure_archive_dir(tmp_path, False, now)
    assert flat == tmp_path


def test_cleanup_old_archives_keeps_cutoff_month(tmp_path):
    for year, month in ((2026, 8), (2026, 9), (2026, 10), (2025, 12)):
        directory = tmp_path / str(year) / f"{month:02d}"
        directory.mkdir(parents=True)
        (directory / "old.xls").write_bytes(b"x")

    removed = cleanup_old_archives(
        tmp_path, retention_months=2, label="流水", now=dt.datetime(2026, 11, 20)
    )

    assert removed == 2  # 2026/08 与 2025/12
    assert not (tmp_path / "2026" / "08").exists()
    assert not (tmp_path / "2025").exists()
    assert (tmp_path / "2026" / "09" / "old.xls").exists()
    assert (tmp_path / "2026" / "10" / "old.xls").exists()


def test_cleanup_old_archives_january_wraps_year(tmp_path):
    (tmp_path / "2025" / "10").mkdir(parents=True)
    (tmp_path / "2025" / "11").mkdir(parents=True)
    (tmp_path / "2025" / "12").mkdir(parents=True)
    (tmp_path / "2026" / "01").mkdir(parents=True)

    removed = cleanup_old_archives(
        tmp_path, retention_months=2, label="回单", now=dt.datetime(2026, 1, 15)
    )

    assert removed == 1  # 截止 2025-11，仅删除更早的 2025/10
    assert not (tmp_path / "2025" / "10").exists()
    assert (tmp_path / "2025" / "11").exists()
    assert (tmp_path / "2025" / "12").exists()
    assert (tmp_path / "2026" / "01").exists()


def test_cleanup_old_archives_disabled(tmp_path):
    (tmp_path / "2020" / "01").mkdir(parents=True)
    assert cleanup_old_archives(tmp_path, retention_months=0) == 0
    assert (tmp_path / "2020" / "01").exists()


def test_cleanup_old_archives_missing_root(tmp_path):
    assert cleanup_old_archives(tmp_path / "missing", retention_months=2) == 0
