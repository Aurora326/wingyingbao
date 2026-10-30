from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "UKeyAgent"


def _base_dir(env_name: str, fallback: Path) -> Path:
    value = os.getenv(env_name)
    return Path(value) if value else fallback


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def config_dir() -> Path:
    path = _base_dir("APPDATA", Path.home() / ".config") / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def local_dir() -> Path:
    path = _base_dir("LOCALAPPDATA", Path.home() / ".local" / "share") / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return config_dir() / "config.json"


def log_dir() -> Path:
    path = local_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class AgentConfig:
    platform_url: str = "https://your-platform.example.com"
    agent_token: str = ""
    agent_code: str = "cust-1517"
    agent_name: str = "客户机房采集盒子"
    ter_ids: str = ""
    mb_exe: str = r"C:\mb\mb.exe"
    mb_user: str = ""
    gf3_exe: str = "gf3"
    collector_root: str = ""
    work_dir: str = ""
    python_exe: str = ""
    report_interval: int = 300
    task_interval: int = 15
    task_timeout: int = 900
    auto_start_workers: bool = True
    autostart: bool = False
    extras: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "AgentConfig":
        path = config_path()
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        known = {field_name for field_name in cls.__dataclass_fields__}
        payload = {key: value for key, value in data.items() if key in known}
        return cls(**payload)

    def save(self) -> None:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    def python(self) -> str:
        if self.python_exe and Path(self.python_exe).exists():
            return self.python_exe
        root = app_root()
        for candidate in (
            root / "runtime" / "Scripts" / "python.exe",
            root / "runtime" / "python.exe",
        ):
            if candidate.exists():
                return str(candidate)
        if not getattr(sys, "frozen", False):
            return sys.executable
        return "python"

    def collectors(self) -> Path:
        root = app_root()
        candidates = [
            Path(self.collector_root) if self.collector_root else None,
            root / "collectors",
            root.parent / "collectors",
        ]
        for candidate in candidates:
            if candidate and (candidate / "bankcore" / "run_task.py").exists():
                return candidate
        return Path(self.collector_root) if self.collector_root else root / "collectors"

    def work(self) -> Path:
        path = Path(self.work_dir) if self.work_dir else local_dir() / "work"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def agent_script(self, name: str) -> Path:
        return app_root() / "agent" / name
