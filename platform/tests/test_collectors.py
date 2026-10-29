from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.services import collectors


def test_unconfigured_bank_raises():
    """没配清单就明确报错，不猜任何默认路径。"""
    with pytest.raises(collectors.CollectorNotConfigured) as exc:
        collectors.resolve("icbc", registry={})
    assert "icbc" in str(exc.value)


def test_registry_points_to_exe(tmp_path):
    exe = tmp_path / "icbc-collector.exe"
    exe.write_text("")
    cmd = collectors.resolve("icbc", registry={"icbc": {"command": [str(exe)], "cwd": str(tmp_path)}})
    assert cmd.argv == [str(exe)]
    assert cmd.mode == "exe"
    assert cmd.origin == "configured"
    assert cmd.cwd == tmp_path


def test_string_command_is_split():
    cmd = collectors.resolve("icbc", registry={"icbc": {"command": "python -m common.collect.run_task"}})
    assert cmd.argv == ["python", "-m", "common.collect.run_task"]


def test_other_banks_unaffected():
    cmd = collectors.resolve("icbc", registry={"icbc": {"command": ["icbc-collector.exe"], "cwd": "."}})
    assert cmd.origin == "configured"


def test_bad_registry_format_raises(tmp_path):
    path = tmp_path / "collectors.yaml"
    path.write_text("- 不是映射\n", encoding="utf-8")
    with pytest.raises(ValueError):
        collectors.load_registry(path)


def test_missing_registry_returns_empty(tmp_path):
    assert collectors.load_registry(tmp_path / "collectors.yaml") == {}


def test_describe_reports_entry_and_mtime(tmp_path):
    entry = tmp_path / "run_task.py"
    entry.write_text("")
    cmd = collectors.CollectorCommand(
        [sys.executable, "-m", "common.collect.run_task"], tmp_path, "source", "default"
    )
    text = collectors.describe(cmd, tmp_path / "task.json")
    assert "run_task.py mtime=" in text
    assert f"cwd={tmp_path}" in text
    assert "来源=default/source" in text
