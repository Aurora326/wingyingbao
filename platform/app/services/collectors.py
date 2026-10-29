"""采集程序清单：决定跑哪份程序、在哪个目录跑。

开发机和客户机跑的是同一个契约 —— 平台在命令行末尾追加 ``--task <task.json>``，
采集程序把结果写到 task.json 里的 result_path。所以「开发者模式」不是第二套代码，
只是清单里那行命令用 ``python -m bankcore.run_task`` 而不是编好的 exe；
没有清单文件时默认就是源码模式。
"""
from __future__ import annotations

import datetime as dt
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.config import settings

# 平台与采集程序之间的契约版本。两侧各自独立发版，版本不一致时要能看出来，
# 而不是表现成一堆莫名其妙的空字段。
CONTRACT_VERSION = 1


@dataclass(frozen=True)
class CollectorCommand:
    argv: list[str]
    cwd: Path
    mode: str      # source / exe
    origin: str    # configured / default


def load_registry(path: Path) -> dict:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"采集程序清单格式错误，应为「银行代码: 配置」的映射: {path}")
    return data


def _as_argv(command) -> list[str]:
    if isinstance(command, str):
        return shlex.split(command)
    return [str(part) for part in command]


def resolve(bank_code: str, python_exec: str = "", registry: dict | None = None) -> CollectorCommand:
    """没配清单就走源码模式：开发机不需要任何额外文件。"""
    if registry is None:
        registry = load_registry(settings.collectors_file)
    entry = registry.get(bank_code) or {}
    command = entry.get("command")
    if not command:
        python = python_exec or settings.collector_python or sys.executable
        return CollectorCommand(
            [python, "-m", "bankcore.run_task"], settings.collectors_root, "source", "default"
        )
    argv = _as_argv(command)
    cwd = Path(entry.get("cwd") or settings.base_dir)
    mode = "exe" if argv[0].lower().endswith(".exe") else "source"
    return CollectorCommand(argv, cwd, mode, "configured")


def describe(command: CollectorCommand, task_file: Path) -> str:
    """任务日志开头的一行：客户现场用来确认跑的到底是哪一份程序。"""
    fields = [
        "采集程序: " + " ".join(command.argv) + f" --task {task_file}",
        f"cwd={command.cwd}",
        f"来源={command.origin}/{command.mode}",
    ]
    stamp = command.cwd / "bankcore" / "run_task.py"
    if not stamp.is_file():
        stamp = Path(command.argv[0])
    if stamp.is_file():
        modified = dt.datetime.fromtimestamp(stamp.stat().st_mtime)
        fields.append(f"{stamp.name} mtime={modified:%Y-%m-%d %H:%M:%S}")
    return " | ".join(fields)
