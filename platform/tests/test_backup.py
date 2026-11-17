from __future__ import annotations

import os
import time

from app.services.backup import copy_new_files, list_backups, prune_backups


def test_copy_new_files_skips_same_size(tmp_path):
    source = tmp_path / "artifacts"
    source.mkdir()
    (source / "a.txt").write_text("hello", encoding="utf-8")
    nested = source / "2026" / "02"
    nested.mkdir(parents=True)
    (nested / "b.txt").write_text("world", encoding="utf-8")

    target = tmp_path / "backup"
    assert copy_new_files(source, target) == 2
    assert copy_new_files(source, target) == 0
    (source / "a.txt").write_text("hello!", encoding="utf-8")
    assert copy_new_files(source, target) == 1


def test_prune_backups(tmp_path):
    root = tmp_path / "backups"
    stale = root / "old"
    fresh = root / "new"
    stale.mkdir(parents=True)
    fresh.mkdir(parents=True)

    old_time = time.time() - 60 * 24 * 3600
    os.utime(stale, (old_time, old_time))

    assert prune_backups(root, keep_days=14) == 1
    assert not stale.exists()
    assert fresh.exists()


def test_list_backups_reads_directories(tmp_path):
    root = tmp_path / "backups"
    entry = root / "20260101_020000"
    entry.mkdir(parents=True)
    (entry / "app_20260101_020000.dump").write_bytes(b"dump")

    items = list_backups(root)
    assert items[0]["name"] == "20260101_020000"
    assert items[0]["dumps"] == ["app_20260101_020000.dump"]
    assert items[0]["size"] == 4
