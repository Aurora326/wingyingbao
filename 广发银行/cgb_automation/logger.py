# -*- coding: utf-8 -*-
"""
日志配置模块（基于 common 的银行适配版）
"""
import datetime
from common.logger import setup_logger as _setup_logger, cleanup_old_logs


def setup_logger(log_level: str = "INFO", log_path: str = None, retention_days: int = 30):
    if log_path is None:
        today = datetime.datetime.now().strftime("%Y%m%d")
        log_path = f"./logs/cgb_automation_{today}.log"
    return _setup_logger(log_level, log_path, retention_days)
