# -*- coding: utf-8 -*-
"""
日志配置模块
"""
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path
from loguru import logger


def cleanup_old_logs(log_dir: Path, retention_days: int = 30):
    """
    清理指定目录下超过保留天数的日志文件
    
    Args:
        log_dir: 日志目录路径
        retention_days: 保留天数，默认30天
    """
    if not log_dir.exists():
        return
    
    cutoff_date = datetime.now() - timedelta(days=retention_days)
    deleted_count = 0
    
    for log_file in log_dir.iterdir():
        if not log_file.is_file():
            continue
        
        # 检查文件是否是日志文件（.log 扩展名）
        if not log_file.suffix.lower() == '.log':
            continue
        
        try:
            # 获取文件修改时间
            file_mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
            
            # 如果文件修改时间早于截止日期，则删除
            if file_mtime < cutoff_date:
                log_file.unlink()
                deleted_count += 1
                print(f"[日志清理] 已删除旧日志: {log_file.name}")
        except Exception as e:
            print(f"[日志清理] 删除 {log_file.name} 失败: {e}")
    
    if deleted_count > 0:
        print(f"[日志清理] 共清理 {deleted_count} 个旧日志文件")


def setup_logger(log_level: str = "INFO", log_path: str = None, retention_days: int = 30):
    """
    配置日志记录器，每天生成新的日志文件
    
    Args:
        log_level: 日志级别
        log_path: 日志文件路径
        retention_days: 日志保留天数，自动清理超过此天数的旧日志
    """
    # 如果没有指定路径，使用按日期命名的文件名
    if log_path is None:
        today = datetime.now().strftime("%Y%m%d")
        log_path = f"./logs/citic_automation_{today}.log"
    
    # 确保日志目录存在
    log_file = Path(log_path)
    log_dir = log_file.parent
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # 清理旧日志
    cleanup_old_logs(log_dir, retention_days)
    
    # 移除默认处理器
    logger.remove()
    
    # 添加控制台输出（仅在非windowed模式或有stdout时）
    if sys.stdout is not None:
        try:
            logger.add(
                sys.stdout,
                level=log_level,
                format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
                colorize=True
            )
        except Exception:
            pass  # 如果控制台输出失败，忽略
    
    # 添加文件输出（按日期命名，每天新建一份）
    logger.add(
        log_path,
        level=log_level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} - {message}",
        rotation="1 day",  # 每天轮转
        encoding="utf-8",
        enqueue=True,  # 使用队列异步写入，避免文件锁问题
        backtrace=True,  # 记录完整堆栈
        diagnose=True
    )
    
    return logger
