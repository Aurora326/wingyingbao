from __future__ import annotations

from app.api.tasks import clean_log, parse_log_entries


def test_clean_log_strips_ansi_and_normalizes_newlines():
    raw = "\x1b[32m2026-10-30 10:05:15\x1b[0m \x1b[1mINFO\x1b[0m 机柜控制器初始化\r\n第二行"
    assert clean_log(raw) == "2026-10-30 10:05:15 INFO 机柜控制器初始化\n第二行"


def test_clean_log_handles_empty():
    assert clean_log(None) == ""
    assert clean_log("") == ""


def test_parse_log_entries_splits_time_level_source_and_text():
    raw = (
        "\x1b[32m2026-11-02 17:15:02\x1b[0m | \x1b[1mINFO    \x1b[0m | "
        "\x1b[36mcommon.cabinet_controller\x1b[0m:\x1b[36m500\x1b[0m - "
        "\x1b[1m创建机柜控制器: mb\x1b[0m"
    )
    entries = parse_log_entries(raw)
    assert entries == [
        {
            "time": "2026-11-02 17:15:02",
            "level": "INFO",
            "source": "common.cabinet_controller:500",
            "text": "创建机柜控制器: mb",
        }
    ]


def test_parse_log_entries_keeps_dash_prefixed_message_intact():
    raw = (
        "2026-11-02 17:15:02 | INFO | common.cabinet_controller:67 - "
        "  - 机柜编号(ter_id): 068330001533"
    )
    entries = parse_log_entries(raw)
    assert entries[0]["source"] == "common.cabinet_controller:67"
    assert entries[0]["text"] == "- 机柜编号(ter_id): 068330001533"


def test_parse_log_entries_keeps_untimed_lines_under_previous_time():
    raw = (
        "2026-11-02 17:15:02 | INFO | main:101 - 开始采集任务\n"
        "\n"
        "Traceback (most recent call last):\n"
        "2026-11-02 17:15:05 | WARNING | main:120 - 重试一次"
    )
    entries = parse_log_entries(raw)
    assert [entry["text"] for entry in entries] == [
        "开始采集任务",
        "Traceback (most recent call last):",
        "重试一次",
    ]
    assert entries[1]["time"] == "2026-11-02 17:15:02"
    assert entries[1]["level"] == "OTHER"
    assert entries[2]["level"] == "WARNING"
