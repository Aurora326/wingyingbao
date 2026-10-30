"""采集程序清单：决定跑哪份程序、在哪个目录跑。

开发机和客户机跑的是同一个契约 —— 平台在命令行末尾追加 ``--task <task.json>``，
采集程序把结果写到 task.json 里的 result_path。所以「开发者模式」不是第二套代码，
只是清单里那行命令用 ``python -m common.collect.run_task`` 而不是编好的 exe；
没有清单文件时默认就是源码模式。
"""
from __future__ import annotations

import datetime as dt
import shlex
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.config import settings

# 平台与采集程序之间的契约版本。两侧各自独立发版，版本不一致时要能看出来，
# 而不是表现成一堆莫名其妙的空字段。
CONTRACT_VERSION = 1


class CollectorNotConfigured(RuntimeError):
    """清单里没有这家银行，提示怎么加，而不是瞎猜路径。"""

    def __init__(self, bank_code: str, path: Path) -> None:
        super().__init__(
            f"银行 {bank_code} 没有配置采集程序。请在 {path} 里加一条：\n"
            f"  {bank_code}:\n"
            '    command: ["python", "run_task.py"]\n'
            "    cwd: <该银行项目目录>"
        )


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
        # Windows 路径里的反斜杠会被 posix 模式的 shlex 当成转义吃掉，
        # 所以用 posix=False，再手工去掉引号
        parts = shlex.split(command, posix=False)
        return [part[1:-1] if len(part) >= 2 and part[0] == part[-1] == '"' else part for part in parts if part]
    return [str(part) for part in command]


def resolve(bank_code: str, registry: dict | None = None) -> CollectorCommand:
    """没配清单就走源码模式：开发机不需要任何额外文件。"""
    if registry is None:
        registry = load_registry(settings.collectors_file)
    entry = registry.get(bank_code) or {}
    command = entry.get("command")
    if not command:
        raise CollectorNotConfigured(bank_code, settings.collectors_file)
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
    stamp = command.cwd / "run_task.py"
    if not stamp.is_file():
        stamp = Path(command.argv[0])
    if stamp.is_file():
        modified = dt.datetime.fromtimestamp(stamp.stat().st_mtime)
        fields.append(f"{stamp.name} mtime={modified:%Y-%m-%d %H:%M:%S}")
    return " | ".join(fields)


def _entry_file(argv: list[str], cwd: Path) -> Path | None:
    """从命令行里找出实际的程序文件（python run_task.py / xxx.exe）。"""
    for part in argv:
        if part.lower().endswith((".py", ".exe")):
            path = Path(part)
            return path if path.is_absolute() else cwd / path
    return None


def status_rows(registry: dict | None = None) -> list[dict]:
    """界面用：每家银行配了哪个采集程序、当前能不能跑。"""
    from app.services.banks import BANK_NAMES

    if registry is None:
        registry = load_registry(settings.collectors_file)
    rows: list[dict] = []
    for code, name in BANK_NAMES.items():
        entry = registry.get(code) or {}
        command = entry.get("command")
        if not command:
            rows.append(
                {"code": code, "name": name, "command": "", "cwd": "", "state": "unconfigured", "detail": "未配置"}
            )
            continue

        argv = _as_argv(command)
        cwd = Path(entry.get("cwd") or settings.base_dir)
        row = {"code": code, "name": name, "command": " ".join(argv), "cwd": str(cwd)}
        if not cwd.is_dir():
            row.update(state="missing", detail="工作目录不存在")
        else:
            target = _entry_file(argv, cwd)
            if target is not None and not target.is_file():
                row.update(state="missing", detail=f"入口不存在: {target.name}")
            else:
                row.update(state="ready", detail="就绪")
        rows.append(row)
    return rows
