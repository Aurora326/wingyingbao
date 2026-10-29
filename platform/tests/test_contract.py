from __future__ import annotations

from app.services import collectors
from common.collect import run_task as collector_entry


def test_contract_versions_match():
    """两侧各持一份版本号，必须一致；不一致时平台会在任务日志里点出来。"""
    assert collector_entry.CONTRACT_VERSION == collectors.CONTRACT_VERSION


def test_credentials_come_from_env(monkeypatch):
    """task.json 会留在磁盘上供排查，所以密钥只走环境变量。"""
    monkeypatch.setenv("BANK_USERNAME", "user1")
    monkeypatch.setenv("BANK_PASSWORD", "pass1")
    monkeypatch.setenv("BANK_PIN", "123456")
    task = {}
    collector_entry.apply_credentials(task)
    assert task["credentials"] == {"username": "user1", "password": "pass1", "pin": "123456"}


def test_task_credentials_kept_when_env_missing(monkeypatch):
    """环境变量没给时保留任务里的值，方便手工调试。"""
    for name in ("BANK_USERNAME", "BANK_PASSWORD", "BANK_PIN"):
        monkeypatch.delenv(name, raising=False)
    task = {"credentials": {"username": "u", "password": "p", "pin": "9"}}
    collector_entry.apply_credentials(task)
    assert task["credentials"]["pin"] == "9"
