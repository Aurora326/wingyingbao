# -*- coding: utf-8 -*-
"""
日志配置模块
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from loguru import logger


def cleanup_old_logs(log_dir: Path, retention_days: int = 30):
    """清理超过保留天数的日志文件"""
    if not log_dir.exists():
        return

    cutoff_date = datetime.now() - timedelta(days=retention_days)
    deleted_count = 0

    for log_file in log_dir.iterdir():
        if not log_file.is_file() or log_file.suffix.lower() != '.log':
            continue
        try:
            file_mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
            if file_mtime < cutoff_date:
                log_file.unlink()
                deleted_count += 1
                print(f"[日志清理] 已删除旧日志: {log_file.name}")
        except Exception as e:
            print(f"[日志清理] 删除 {log_file.name} 失败: {e}")

    if deleted_count > 0:
        print(f"[日志清理] 共清理 {deleted_count} 个旧日志文件")


def setup_logger(log_level: str = "INFO", log_path: str = None, retention_days: int = 30):
    """配置日志记录器，每天生成新的日志文件"""
    if log_path is None:
        today = datetime.now().strftime("%Y%m%d")
        log_path = f"./logs/icbc_automation_{today}.log"

    log_file = Path(log_path)
    log_dir = log_file.parent
    log_dir.mkdir(parents=True, exist_ok=True)

    cleanup_old_logs(log_dir, retention_days)

    logger.remove()

    if sys.stdout is not None:
        try:
            logger.add(
                sys.stdout,
                level=log_level,
                format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
                colorize=True
            )
        except Exception:
            pass

    logger.add(
        log_path,
        level=log_level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} - {message}",
        rotation="1 day",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=True
    )

    return logger
