from __future__ import annotations

import sqlite3

from backup import backup_database, copy_new_files, create_backup, prune_backups


def test_backup_database_copies_rows(tmp_path):
    db_path = tmp_path / "app.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE demo (id INTEGER PRIMARY KEY, name TEXT)")
    connection.execute("INSERT INTO demo (name) VALUES ('流水')")
    connection.commit()
    connection.close()

    target = backup_database(db_path, tmp_path / "out")
    assert target.exists()
    restored = sqlite3.connect(target)
    try:
        assert restored.execute("SELECT name FROM demo").fetchone()[0] == "流水"
    finally:
        restored.close()


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


def test_create_backup_and_prune(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "app.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE t (id INTEGER)")
    connection.commit()
    connection.close()
    artifacts = data_dir / "artifacts" / "2026" / "02"
    artifacts.mkdir(parents=True)
    (artifacts / "file.pdf").write_bytes(b"%PDF-1.4")

    target_root = data_dir / "backups"
    backup = create_backup(db_path, data_dir / "artifacts", target_root, keep_days=14)
    assert (backup / "app.db").exists()
    assert (backup / "artifacts" / "2026" / "02" / "file.pdf").exists()

    stale = target_root / "old"
    stale.mkdir()
    import os
    import time

    old_time = time.time() - 60 * 24 * 3600
    os.utime(stale, (old_time, old_time))
    assert prune_backups(target_root, keep_days=14) == 1
    assert not stale.exists()
