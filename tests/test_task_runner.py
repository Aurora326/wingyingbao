from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from common import task_runner


def _fake_config():
    return SimpleNamespace(
        collection=SimpleNamespace(
            statements=SimpleNamespace(start_date="", end_date=""),
            receipts=SimpleNamespace(start_date="", end_date=""),
            payments=SimpleNamespace(
                enabled=False,
                action="submit",
                batch_file="",
                items=[],
                payer_account="",
                payer_name="",
            ),
        ),
        credentials=SimpleNamespace(username="", password="", pin=""),
        cabinet=SimpleNamespace(
            type="mock",
            port=0,
            manual_mode=True,
            ter_id="",
            user="",
            mb_path="",
        ),
    )


def test_apply_task_overrides_dates_credentials_and_cabinet(tmp_path, monkeypatch):
    monkeypatch.setenv("BANK_USERNAME", "user")
    monkeypatch.setenv("BANK_PASSWORD", "pass")
    monkeypatch.setenv("BANK_PIN", "pin")
    config = _fake_config()
    task = {
        "start_date": "2026-10-01",
        "end_date": "2026-10-31",
        "gf3_port": 3,
        "device_backend": "mb",
        "ter_id": "068330001533",
        "mb_user": "15711668178",
        "mb_exe": r"C:\wangyingbao\m2\mb.exe",
    }
    task_runner.apply_task_overrides(config, task)

    assert config.collection.statements.start_date == "2026-10-01"
    assert config.collection.statements.end_date == "2026-10-31"
    assert (config.credentials.username, config.credentials.password, config.credentials.pin) == (
        "user",
        "pass",
        "pin",
    )
    assert config.cabinet.type == "mb"
    assert config.cabinet.port == 3
    assert config.cabinet.manual_mode is False
    assert config.cabinet.ter_id == "068330001533"
    assert config.cabinet.user == "15711668178"
    assert config.cabinet.mb_path.endswith("mb.exe")


def test_apply_draft_settings_forces_save_and_items(tmp_path):
    config = _fake_config()
    items = [{"payee_account": "A1"}]
    batch = tmp_path / "batch.json"
    task = {"payer_account_no": "ACC", "payer_name": "NAME"}
    task_runner.apply_draft_settings(config, task, items, batch)

    payments = config.collection.payments
    assert payments.enabled is True
    assert payments.action == "save"
    assert payments.batch_file == str(batch)
    assert payments.items == items
    assert payments.payer_account == "ACC"
    assert payments.payer_name == "NAME"


def test_collect_outputs_splits_no_data_and_files(tmp_path):
    real = tmp_path / "statement.xls"
    real.write_bytes(b"x")
    marker = tmp_path / "建设银行_银行流水_无数据_1.txt"
    marker.write_text("no data", encoding="utf-8")

    files, no_data = task_runner.collect_outputs(
        {"statements": [str(real), str(marker), "当前查询结果为空"], "receipts": []}
    )
    assert files == [str(real.resolve())]
    assert no_data is True


def test_write_collect_and_draft_result_payloads(tmp_path):
    collect = tmp_path / "result.json"
    task_runner.write_collect_result(
        collect, task_id=253, ok=True, files=["a"], no_data=True
    )
    payload = json.loads(collect.read_text(encoding="utf-8"))
    assert payload["task_id"] == 253
    assert payload["ok"] is True
    assert payload["no_data"] is True
    assert payload["error_code"] == ""

    draft = tmp_path / "draft.json"
    task_runner.write_draft_result(draft, ok=False, message="失败: x")
    payload = json.loads(draft.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["error_code"] == "DRAFT_FAILED"
    assert payload["bank_batch_no"] == ""
    assert "no_data" not in payload


def test_diagnostics_dir_defaults_to_work_dir(tmp_path):
    task = {"work_dir": str(tmp_path)}
    directory = task_runner.diagnostics_dir(task, tmp_path / "task.json")
    assert directory == tmp_path / "diagnostics"
    assert directory.is_dir()

    explicit = tmp_path / "diag"
    directory = task_runner.diagnostics_dir({"diagnostics_dir": str(explicit)}, tmp_path / "task.json")
    assert directory == explicit
    assert directory.is_dir()


class _FakePage:
    url = "https://example.test/login"
    frames: list = []

    def title(self):
        return "示例页面"

    def content(self):
        return "<html><body>ok</body></html>"

    def screenshot(self, path, **kwargs):
        Path(path).write_bytes(b"jpeg")


def test_capture_failure_writes_diagnostic_files(tmp_path):
    files = task_runner.capture_failure(
        _FakePage(), tmp_path, stage="unit", error_code="COLLECT_FAILED", message="boom"
    )
    suffixes = {Path(item).name.split("_")[0] for item in files}
    assert "failure" in suffixes
    assert any(item.endswith(".jpg") for item in files)
    assert any(item.endswith(".html") for item in files)
    meta = next(item for item in files if item.endswith("_meta.json") or "_meta_" in item)
    import json as _json

    payload = _json.loads(Path(meta).read_text(encoding="utf-8"))
    assert payload["kind"] == "failure"
    assert payload["error_code"] == "COLLECT_FAILED"
