# -*- coding: utf-8 -*-
"""
配置文件加载模块
支持 INI、JSON 和 YAML 格式，优先使用 INI
"""
import json
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Union


@dataclass
class CredentialsConfig:
    """登录凭证配置"""
    username: str = ""           # 登录用户名
    password: str = ""           # 登录密码
    pin: str = ""                # UKey PIN 码（6-12位）


@dataclass
class StatementsConfig:
    """流水采集配置"""
    enabled: bool = True              # 是否启用流水采集
    days_back: int = 30               # 查询起始日期（天），当 start_date 为空时生效
    start_date: str = ""              # 自定义查询起始日期，格式：YYYY-MM-DD
    end_date: str = ""                # 自定义查询结束日期，格式：YYYY-MM-DD
    accounts: List[str] = field(default_factory=list)
    file_format: str = "excel"        # 文件格式: excel, pdf, csv
    download_path: str = "./downloads/statements"
    archive_by_month: bool = True     # 是否按年月归档
    retention_months: int = 2         # 保留最近几个月的数据


@dataclass
class ReceiptsConfig:
    """电子回单采集配置"""
    enabled: bool = True              # 是否启用回单采集
    days_back: int = 30
    accounts: List[str] = field(default_factory=list)
    download_path: str = "./downloads/receipts"
    organize_by_date: bool = True
    archive_by_month: bool = True     # 是否按年月归档
    retention_months: int = 2         # 保留最近几个月的数据


@dataclass
class CollectionConfig:
    """采集配置"""
    statements: StatementsConfig = field(default_factory=StatementsConfig)
    receipts: ReceiptsConfig = field(default_factory=ReceiptsConfig)


@dataclass
class BrowserConfig:
    """浏览器配置"""
    login_url: str = "https://ebank.cgbchina.com.cn/corporbank/"
    headless: bool = False
    chrome_path: str = ""
    extension_path: str = ""           # 广发银行可能需要特定的安全控件
    extension_id: str = ""
    download_timeout: int = 60
    page_timeout: int = 30


@dataclass
class OCRConfig:
    """OCR验证码识别配置"""
    enabled: bool = False              # 是否启用自动识别
    api_key: str = ""                  # 百度OCR API Key
    secret_key: str = ""               # 百度OCR Secret Key
    fallback_manual: bool = True       # 自动识别失败时是否允许手动输入


@dataclass
class MBConfig:
    """MB智能机柜控制配置"""
    enabled: bool = False              # 是否启用机柜控制
    mb_path: str = "mb"                # mb命令路径
    ter_id: str = ""                   # 机柜编号
    port: int = 1                      # 端口号
    user: str = "test"                 # 用户名


@dataclass
class RuntimeConfig:
    """运行配置"""
    log_level: str = "INFO"
    log_path: str = None  # 默认为None，使用按日期命名的日志文件
    max_retries: int = 3
    retry_delay: int = 5
    auto_close: bool = False
    log_retention_days: int = 30  # 日志保留天数，超过此天数的日志自动删除


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
    ocr: OCRConfig = field(default_factory=OCRConfig)
    mb: MBConfig = field(default_factory=MBConfig)


def _load_ini_config(config_path: Path) -> dict:
    """从 INI 文件加载配置"""
    import configparser
    
    parser = configparser.ConfigParser()
    parser.read(config_path, encoding='utf-8')
    
    def get_value(section, key, default=None, value_type=str):
        """获取配置值并转换类型"""
        try:
            value = parser.get(section, key, fallback=default)
            if value is None:
                return default
            if value_type == bool:
                return value.lower() in ('true', 'yes', '1', 'on')
            elif value_type == int:
                return int(value)
            elif value_type == list:
                # 逗号分隔的列表
                return [x.strip() for x in value.split(',') if x.strip()] if value else []
            return value
        except:
            return default
    
    # 构建配置字典
    data = {
        'credentials': {
            'username': get_value('credentials', 'username', ''),
            'password': get_value('credentials', 'password', ''),
            'pin': get_value('credentials', 'pin', ''),
        },
        'collection': {
            'statements': {
                'enabled': get_value('collection.statements', 'enabled', True, bool),
                'days_back': get_value('collection.statements', 'days_back', 30, int),
                'start_date': get_value('collection.statements', 'start_date', ''),
                'end_date': get_value('collection.statements', 'end_date', ''),
                'accounts': get_value('collection.statements', 'accounts', [], list),
                'file_format': get_value('collection.statements', 'file_format', 'excel'),
                'download_path': get_value('collection.statements', 'download_path', './downloads/statements'),
                'archive_by_month': get_value('collection.statements', 'archive_by_month', True, bool),
                'retention_months': get_value('collection.statements', 'retention_months', 2, int),
            },
            'receipts': {
                'enabled': get_value('collection.receipts', 'enabled', True, bool),
                'days_back': get_value('collection.receipts', 'days_back', 30, int),
                'accounts': get_value('collection.receipts', 'accounts', [], list),
                'download_path': get_value('collection.receipts', 'download_path', './downloads/receipts'),
                'organize_by_date': get_value('collection.receipts', 'organize_by_date', True, bool),
                'archive_by_month': get_value('collection.receipts', 'archive_by_month', True, bool),
                'retention_months': get_value('collection.receipts', 'retention_months', 2, int),
            }
        },
        'browser': {
            'login_url': get_value('browser', 'login_url', 'https://ebank.cgbchina.com.cn/corporbank/'),
            'headless': get_value('browser', 'headless', False, bool),
            'chrome_path': get_value('browser', 'chrome_path', ''),
            'extension_path': get_value('browser', 'extension_path', ''),
            'extension_id': get_value('browser', 'extension_id', ''),
            'download_timeout': get_value('browser', 'download_timeout', 60, int),
            'page_timeout': get_value('browser', 'page_timeout', 30, int),
        },
        'runtime': {
            'log_level': get_value('runtime', 'log_level', 'INFO'),
            'log_path': get_value('runtime', 'log_path', None),
            'max_retries': get_value('runtime', 'max_retries', 1, int),
            'retry_delay': get_value('runtime', 'retry_delay', 5, int),
            'auto_close': get_value('runtime', 'auto_close', False, bool),
            'log_retention_days': get_value('runtime', 'log_retention_days', 30, int),
        },
        'schedule': {
            'enabled': get_value('schedule', 'enabled', False, bool),
            'cron': get_value('schedule', 'cron', '0 9 * * *'),
        },
        'ocr': {
            'enabled': get_value('ocr', 'enabled', False, bool),
            'api_key': get_value('ocr', 'api_key', ''),
            'secret_key': get_value('ocr', 'secret_key', ''),
            'fallback_manual': get_value('ocr', 'fallback_manual', True, bool),
        },
        'mb': {
            'enabled': get_value('mb', 'enabled', False, bool),
            'mb_path': get_value('mb', 'mb_path', 'mb'),
            'ter_id': get_value('mb', 'ter_id', ''),
            'port': get_value('mb', 'port', 1, int),
            'user': get_value('mb', 'user', 'test'),
        }
    }
    
    return data


def load_config(config_path: str = None) -> Config:
    """
    加载配置文件
    
    支持 INI (.ini)、JSON (.json) 和 YAML (.yaml, .yml) 格式
    如果未指定路径，优先查找 config.ini，其次是 config.json
    
    Args:
        config_path: 配置文件路径，为 None 时自动查找
        
    Returns:
        Config 对象
    """
    if config_path is None:
        # 自动查找配置文件，优先 INI，其次 JSON
        for filename in ["config.ini", "config.json", "config.yaml", "config.yml"]:
            if Path(filename).exists():
                config_path = filename
                break
        if config_path is None:
            raise FileNotFoundError("未找到配置文件，请创建 config.ini、config.json 或 config.yaml")
    
    config_file = Path(config_path)
    
    if not config_file.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    
    # 根据文件扩展名选择解析方式
    suffix = config_file.suffix.lower()
    
    if suffix == '.ini':
        data = _load_ini_config(config_file)
    elif suffix == '.json':
        with open(config_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    elif suffix in ['.yaml', '.yml']:
        with open(config_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
    else:
        # 尝试自动检测
        with open(config_file, 'r', encoding='utf-8') as f:
            content = f.read()
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                try:
                    data = yaml.safe_load(content)
                except:
                    # 尝试 INI 格式
                    data = _load_ini_config(config_file)
    
    return Config(
        credentials=CredentialsConfig(**data.get('credentials', {})),
        collection=CollectionConfig(
            statements=StatementsConfig(**data.get('collection', {}).get('statements', {})),
            receipts=ReceiptsConfig(**data.get('collection', {}).get('receipts', {}))
        ),
        browser=BrowserConfig(**data.get('browser', {})),
        runtime=RuntimeConfig(**data.get('runtime', {})),
        schedule=ScheduleConfig(**data.get('schedule', {})),
        ocr=OCRConfig(**data.get('ocr', {})),
        mb=MBConfig(**data.get('mb', {}))
    )


def _save_ini_config(config: Config, config_path: str) -> None:
    """保存配置到 INI 文件"""
    import configparser
    
    parser = configparser.ConfigParser()
    
    # credentials 部分
    parser['credentials'] = {
        'username': config.credentials.username or '',
        'password': config.credentials.password or '',
        'pin': config.credentials.pin or '',
    }
    
    # collection.statements 部分
    stmt = config.collection.statements
    parser['collection.statements'] = {
        'enabled': str(stmt.enabled).lower(),
        'days_back': str(stmt.days_back),
        'start_date': stmt.start_date or '',
        'end_date': stmt.end_date or '',
        'accounts': ','.join(stmt.accounts) if stmt.accounts else '',
        'file_format': stmt.file_format or 'excel',
        'download_path': stmt.download_path or './downloads/statements',
        'archive_by_month': str(stmt.archive_by_month).lower(),
        'retention_months': str(stmt.retention_months),
    }
    
    # collection.receipts 部分
    rcpt = config.collection.receipts
    parser['collection.receipts'] = {
        'enabled': str(rcpt.enabled).lower(),
        'days_back': str(rcpt.days_back),
        'accounts': ','.join(rcpt.accounts) if rcpt.accounts else '',
        'download_path': rcpt.download_path or './downloads/receipts',
        'organize_by_date': str(rcpt.organize_by_date).lower(),
        'archive_by_month': str(rcpt.archive_by_month).lower(),
        'retention_months': str(rcpt.retention_months),
    }
    
    # browser 部分
    br = config.browser
    parser['browser'] = {
        'login_url': br.login_url or 'https://ebank.cgbchina.com.cn/corporbank/',
        'headless': str(br.headless).lower(),
        'chrome_path': br.chrome_path or '',
        'extension_path': br.extension_path or '',
        'extension_id': br.extension_id or '',
        'download_timeout': str(br.download_timeout),
        'page_timeout': str(br.page_timeout),
    }
    
    # runtime 部分
    rt = config.runtime
    parser['runtime'] = {
        'log_level': rt.log_level or 'INFO',
        'log_path': rt.log_path or '',
        'max_retries': str(rt.max_retries),
        'retry_delay': str(rt.retry_delay),
        'auto_close': str(rt.auto_close).lower(),
        'log_retention_days': str(rt.log_retention_days),
    }
    
    # schedule 部分
    sch = config.schedule
    parser['schedule'] = {
        'enabled': str(sch.enabled).lower(),
        'cron': sch.cron or '0 9 * * *',
    }
    
    # ocr 部分
    ocr = config.ocr
    parser['ocr'] = {
        'enabled': str(ocr.enabled).lower(),
        'api_key': ocr.api_key or '',
        'secret_key': ocr.secret_key or '',
        'fallback_manual': str(ocr.fallback_manual).lower(),
    }
    
    # mb 部分
    mb = config.mb
    parser['mb'] = {
        'enabled': str(mb.enabled).lower(),
        'mb_path': mb.mb_path or 'mb',
        'ter_id': mb.ter_id or '',
        'port': str(mb.port),
        'user': mb.user or 'test',
    }
    
    with open(config_path, 'w', encoding='utf-8') as f:
        parser.write(f)


def save_config(config: Config, config_path: str = "config.ini") -> None:
    """
    保存配置到文件
    
    支持 INI、JSON 和 YAML 格式（根据文件扩展名）
    默认为 INI 格式
    
    Args:
        config: 配置对象
        config_path: 保存路径，默认为 config.ini
    """
    from dataclasses import asdict
    
    config_file = Path(config_path)
    suffix = config_file.suffix.lower()
    
    if suffix == '.ini':
        _save_ini_config(config, config_path)
    else:
        config_dict = asdict(config)
        
        with open(config_file, 'w', encoding='utf-8') as f:
            if suffix == '.json':
                json.dump(config_dict, f, indent=2, ensure_ascii=False)
            else:
                yaml.dump(config_dict, f, allow_unicode=True, sort_keys=False)
    
    print(f"配置已保存到: {config_path}")
