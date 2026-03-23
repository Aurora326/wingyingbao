# -*- coding: utf-8 -*-
"""
日志配置模块
"""
import sys
from datetime import datetime
from pathlib import Path
from loguru import logger


def setup_logger(log_level: str = "INFO", log_path: str = None):
    """配置日志记录器，每天生成新的日志文件"""
    # 如果没有指定路径，使用按日期命名的文件名
    if log_path is None:
        today = datetime.now().strftime("%Y%m%d")
        log_path = f"./logs/cib_automation_{today}.log"
    
    # 确保日志目录存在
    log_file = Path(log_path)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    
    # 移除默认处理器
    logger.remove()
    
    # 添加控制台输出
    logger.add(
        sys.stdout,
        level=log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        colorize=True
    )
    
    # 添加文件输出（按日期命名，每天新建一份）
    logger.add(
        log_path,
        level=log_level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} - {message}",
        rotation="1 day",  # 每天轮转
        retention="30 days",  # 保留30天
        encoding="utf-8"
    )
    
    return logger
