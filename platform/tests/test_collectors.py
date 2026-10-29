from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.services import collectors


def test_no_registry_means_source_mode():
    """开发机不配清单：直接用本仓库源码跑，改了就地生效。"""
    cmd = collectors.resolve("icbc", python_exec="python.exe", registry={})
    assert cmd.argv == ["python.exe", "-m", "bankcore.run_task"]
    assert cmd.mode == "source"
    assert cmd.origin == "default"
    assert cmd.cwd == collectors.settings.collectors_root


def test_registry_points_to_exe(tmp_path):
    exe = tmp_path / "icbc-collector.exe"
    exe.write_text("")
    cmd = collectors.resolve("icbc", registry={"icbc": {"command": [str(exe)], "cwd": str(tmp_path)}})
    assert cmd.argv == [str(exe)]
    assert cmd.mode == "exe"
    assert cmd.origin == "configured"
    assert cmd.cwd == tmp_path


def test_string_command_is_split():
    cmd = collectors.resolve("icbc", registry={"icbc": {"command": "python -m bankcore.run_task"}})
    assert cmd.argv == ["python", "-m", "bankcore.run_task"]


def test_unconfigured_bank_falls_back_to_source():
    cmd = collectors.resolve("nbcb", registry={"icbc": {"command": ["icbc-collector.exe"]}})
    assert cmd.origin == "default"
    assert cmd.mode == "source"


def test_bad_registry_format_raises(tmp_path):
    path = tmp_path / "collectors.yaml"
    path.write_text("- 不是映射\n", encoding="utf-8")
    with pytest.raises(ValueError):
        collectors.load_registry(path)


def test_missing_registry_returns_empty(tmp_path):
    assert collectors.load_registry(tmp_path / "collectors.yaml") == {}


def test_describe_reports_entry_and_mtime(tmp_path):
    entry = tmp_path / "bankcore" / "run_task.py"
    entry.parent.mkdir()
    entry.write_text("")
    cmd = collectors.CollectorCommand(
        [sys.executable, "-m", "bankcore.run_task"], tmp_path, "source", "default"
    )
    text = collectors.describe(cmd, tmp_path / "task.json")
    assert "run_task.py mtime=" in text
    assert f"cwd={tmp_path}" in text
    assert "来源=default/source" in text
