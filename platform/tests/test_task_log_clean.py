from __future__ import annotations

from app.api.tasks import clean_log


def test_clean_log_strips_ansi_and_normalizes_newlines():
    raw = "\x1b[32m2026-10-30 10:05:15\x1b[0m \x1b[1mINFO\x1b[0m 机柜控制器初始化\r\n第二行"
    assert clean_log(raw) == "2026-10-30 10:05:15 INFO 机柜控制器初始化\n第二行"


def test_clean_log_handles_empty():
    assert clean_log(None) == ""
    assert clean_log("") == ""
