# -*- coding: utf-8 -*-
"""
配置文件加载模块
"""
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class CredentialsConfig:
    """登录凭证配置"""
    username: str = ""           # 登录用户名（如：物盾龙柏管理员）
    password: str = ""           # 登录密码
    pin: str = ""                # UKey PIN 码（如：WD123456）


@dataclass
class StatementsConfig:
    """流水采集配置"""
    days_back: int = 30               # 查询起始日期（天），当 start_date 为空时生效
    start_date: str = ""              # 自定义查询起始日期，格式：YYYY-MM-DD，如：2025-01-01
    end_date: str = ""                # 自定义查询结束日期，格式：YYYY-MM-DD，如：2025-02-05
    accounts: List[str] = field(default_factory=list)
    file_format: str = "excel"
    download_path: str = "./downloads/statements"


@dataclass
class ReceiptsConfig:
    """电子回单采集配置"""
    days_back: int = 30
    accounts: List[str] = field(default_factory=list)
    download_path: str = "./downloads/receipts"
    organize_by_date: bool = True


@dataclass
class CollectionConfig:
    """采集配置"""
    statements: StatementsConfig = field(default_factory=StatementsConfig)
    receipts: ReceiptsConfig = field(default_factory=ReceiptsConfig)


@dataclass
class BrowserConfig:
    """浏览器配置"""
    login_url: str = "https://www.cib.com.cn/corporate/"
    headless: bool = False
    chrome_path: str = ""
    extension_path: str = "../CryptoKitExtension.CIB.crx"
    extension_id: str = ""
    download_timeout: int = 60
    page_timeout: int = 30


@dataclass
class RuntimeConfig:
    """运行配置"""
    log_level: str = "INFO"
    log_path: str = "./logs/cib_automation.log"
    max_retries: int = 3
    retry_delay: int = 5
    auto_close: bool = False


@dataclass
class ScheduleConfig:
    """定时任务配置"""
    enabled: bool = False
    cron: str = "0 9 * * *"


@dataclass
class Config:
    """完整配置"""
    credentials: CredentialsConfig = field(default_factory=CredentialsConfig)
    collection: CollectionConfig = field(default_factory=CollectionConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)


def load_config(config_path: str = "config.yaml") -> Config:
    """加载配置文件"""
    config_file = Path(config_path)
    
    if not config_file.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    
    with open(config_file, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    
    return Config(
        credentials=CredentialsConfig(**data.get('credentials', {})),
        collection=CollectionConfig(
            statements=StatementsConfig(**data.get('collection', {}).get('statements', {})),
            receipts=ReceiptsConfig(**data.get('collection', {}).get('receipts', {}))
        ),
        browser=BrowserConfig(**data.get('browser', {})),
        runtime=RuntimeConfig(**data.get('runtime', {})),
        schedule=ScheduleConfig(**data.get('schedule', {}))
    )
