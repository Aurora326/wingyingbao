from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT_DIR))

import task_worker  # noqa: E402


def test_execute_task_terminates_collector_on_cancel(tmp_path, monkeypatch):
    collector = tmp_path / "fake_collector.py"
    collector.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    registry = {
        "mock": {
            "command": [sys.executable, "fake_collector.py"],
            "cwd": str(tmp_path),
        }
    }
    registry_file = tmp_path / "collectors.yaml"
    registry_file.write_text(yaml.safe_dump(registry), encoding="utf-8")

    calls = {"count": 0}

    def fake_cancel(_args, _task_id):
        calls["count"] += 1
        return calls["count"] >= 2

    monkeypatch.setattr(task_worker, "cancel_requested", fake_cancel)

    args = SimpleNamespace(
        url="",
        token="",
        agent_code="test-agent",
        work_dir=tmp_path / "work",
        timeout=30,
        mb_exe="mb.exe",
        gf3_exe="gf3",
        mb_user="",
        collector_python=sys.executable,
        collector_root=str(tmp_path),
        collectors_file=str(registry_file),
    )
    task = {
        "task_id": 9999,
        "attempt": 1,
        "bank_code": "mock",
        "account_no": "TEST-0001",
        "type": "statements",
        "start_date": "2026-11-01",
        "end_date": "2026-11-02",
        "credentials": {},
    }

    payload = task_worker.execute_task(task, args)

    assert payload["exit_code"] == 130
    assert payload["result"]["error_code"] == "CANCELLED"
    assert payload["files"] == []
    assert calls["count"] >= 2
