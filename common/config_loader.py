# -*- coding: utf-8 -*-
"""
配置文件加载模块
支持 INI、JSON 和 YAML 格式，优先使用 INI
"""
import json
import re
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
    file_format: str = "pdf"          # 文件格式: pdf
    download_path: str = "./downloads/receipts"
    organize_by_date: bool = True
    archive_by_month: bool = True     # 是否按年月归档
    retention_months: int = 2         # 保留最近几个月的数据


@dataclass
class ReconciliationConfig:
    """电子对账单采集配置"""
    enabled: bool = True              # 是否启用电子对账单采集
    days_back: int = 30               # 查询起始日期（天），当 start_date 为空时生效
    start_date: str = ""              # 自定义查询起始日期，格式：YYYY-MM-DD
    end_date: str = ""                # 自定义查询结束日期，格式：YYYY-MM-DD
    accounts: List[str] = field(default_factory=list)
    file_format: str = "excel"        # 文件格式: excel / csv / pdf
    download_path: str = "./downloads/reconciliation"
    archive_by_month: bool = True     # 是否按年月归档
    retention_months: int = 2         # 保留最近几个月的数据


@dataclass
class PaymentItem:
    """单条制单信息"""
    payer_account: str = ""           # 付款账户（可填部分匹配文本）
    payer_name: str = ""              # 付款方账户名称
    amount: str = ""                  # 付款金额
    currency: str = "人民币"          # 币种
    payee_account: str = ""           # 收方账号
    payee_name: str = ""              # 收方户名
    payee_account_type: str = ""      # 收款账户类型：公司账户/个人账户/信用卡
    payee_bank: str = ""              # 收款银行
    payee_branch: str = ""            # 收款银行开户网点
    payee_bank_code: str = ""         # 支付联行号
    payee_bank_type: str = ""         # 收款方行别代码：01=本行, 02=国内他行
    purpose: str = ""                 # 附言/用途
    remark: str = ""                  # 备注
    reconcile_no: str = ""            # 对账编号
    business_flow: str = ""           # 业务流程（如：审批流程）
    payment_method: str = ""          # 付款方式：跨行转账/行内转账/企业内部转账
    serial_no: str = ""               # 客户方流水号
    notify: str = "0"                 # 是否短信通知收款人：0=不通知, 1=通知
    payee_phone: str = ""             # 收款人手机号码
    notify_msg: str = ""              # 短信通知附加信息


@dataclass
class OfflineImportConfig:
    """南京银行离线明细导入配置"""
    enabled: bool = False              # 是否启用离线明细导入
    source_directory: str = ""       # 模板搜索目录；空则自动定位南京银行 downloads/statements
    filename_pattern: str = "离线账户明细导入_已填_*.xls"
    click_recognize: bool = True     # 上传后是否自动点击发起识别
    wait_recognize_success: bool = True  # 发起识别后是否等待识别成功
    click_submit: bool = True        # 识别成功后是否自动点击提交
    recognize_timeout: int = 30      # 等待识别成功超时（秒）
    recognize_refresh_interval: int = 3  # 识别状态刷新间隔（秒）
    upload_timeout: int = 30         # 等待上传超时（秒）


@dataclass
class PaymentsConfig:
    """制单配置"""
    enabled: bool = False             # 是否启用制单
    action: str = "save"              # 操作类型: save=暂存, submit=提交
    batch_file: str = ""              # 批量制单文件路径(JSON/CSV)，为空则使用单条配置
    template_file: str = ""           # 批量导入模板路径
    template_mapping_file: str = ""   # 批量导入模板字段映射路径
    generated_file: str = ""          # 自动生成的导入文件路径
    items: List[PaymentItem] = field(default_factory=list)  # 批量制单列表（内存中）
    # 单条制单快捷配置（当 batch_file 为空且 items 为空时使用）
    payer_account: str = ""
    payer_name: str = ""              # 付款方账户名称
    amount: str = ""
    currency: str = "人民币"
    payee_account: str = ""
    payee_name: str = ""
    payee_account_type: str = ""
    payee_bank: str = ""
    payee_branch: str = ""
    payee_bank_code: str = ""
    payee_bank_type: str = "02"       # 收款方行别代码：01=本行, 02=国内他行
    purpose: str = ""
    remark: str = ""
    reconcile_no: str = ""
    business_flow: str = ""
    payment_method: str = ""
    serial_no: str = ""               # 客户方流水号
    notify: str = "0"                 # 是否短信通知：0=不通知, 1=通知
    payee_phone: str = ""             # 收款人手机号码
    notify_msg: str = ""              # 短信通知附加信息
    # 批量转账页面公共选项（建设银行等）
    batch_name: str = ""              # 批量名称
    verify_name: bool = False         # 行内收款账户户名校验
    prefer_realtime: bool = False     # 优先采用跨行实时转账


@dataclass
class CollectionConfig:
    """采集配置"""
    statements: StatementsConfig = field(default_factory=StatementsConfig)
    receipts: ReceiptsConfig = field(default_factory=ReceiptsConfig)
    reconciliation: ReconciliationConfig = field(default_factory=ReconciliationConfig)
    payments: PaymentsConfig = field(default_factory=PaymentsConfig)
    offline_import: OfflineImportConfig = field(default_factory=OfflineImportConfig)


@dataclass
class BrowserConfig:
    """浏览器配置"""
    login_url: str = ""
    headless: bool = False
    chrome_path: str = ""
    extension_path: str = ""           # 广发银行可能需要特定的安全控件
    extension_id: str = ""
    download_timeout: int = 60
    page_timeout: int = 30


@dataclass
class MBConfig:
    """MB智能机柜控制配置（兼容旧版，推荐使用 [cabinet] 段）"""
    enabled: bool = False              # 是否启用机柜控制
    type: str = "mb"                   # 机柜类型: mb|gf3|mock|other
    mb_path: str = "mb"                # mb命令路径
    ter_id: str = ""                   # 机柜编号
    port: int = 1                      # 端口号
    user: str = "test"                 # 用户名


@dataclass
class CabinetConfig:
    """机柜控制器配置（新版，支持多供应商）"""
    type: str = "mb"                   # 机柜类型: mb|gf3|mock|other
    manual_mode: bool = False          # 手动模式（不控制机柜）
    # MB机柜配置
    mb_path: str = "mb"
    ter_id: str = ""
    port: int = 1
    user: str = "test"
    # GF3机柜配置
    gf3_path: str = "gf3.exe"
    product_id: int = 1
    # 其他供应商配置
    api_url: str = ""
    api_key: str = ""
    device_id: str = ""


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
class CaptchaConfig:
    """验证码处理配置"""
    enabled: bool = False              # 是否启用验证码自动识别
    api_key: str = ""                  # 百度云 API Key
    secret_key: str = ""               # 百度云 Secret Key
    dialog_selector: str = ""          # 验证码弹窗选择器（可选）
    input_selector: str = ""           # 验证码输入框选择器（可选）
    img_selector: str = ""             # 验证码图片选择器（可选）
    submit_selector: str = ""          # 提交按钮选择器（可选）


@dataclass
class Config:
    """完整配置"""
    credentials: CredentialsConfig = field(default_factory=CredentialsConfig)
    collection: CollectionConfig = field(default_factory=CollectionConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    captcha: CaptchaConfig = field(default_factory=CaptchaConfig)
    mb: MBConfig = field(default_factory=MBConfig)  # 兼容旧版
    cabinet: CabinetConfig = field(default_factory=CabinetConfig)  # 新版机柜配置
    
    def get_cabinet_config(self):
        """获取机柜配置（兼容新旧配置）"""
        # 优先使用新版 cabinet 配置（检查是否有有效配置）
        # 如果 ter_id 有值，或者 type 不是默认的 mb（如 gf3/mock/other），使用 cabinet 配置
        if self.cabinet and (self.cabinet.ter_id or self.cabinet.type in ["gf3", "mock", "other"]):
            return self.cabinet
        # 如果 cabinet 有配置但 ter_id 为空，且 mb 未启用，也使用 cabinet（type=mb 的情况）
        if self.cabinet and self.cabinet.type == "mb" and not (self.mb and self.mb.enabled):
            # 检查是否从配置文件加载了 cabinet 段（ter_id 可能为空但意图使用 cabinet）
            # 这种情况下使用 cabinet 配置
            return self.cabinet
        # 兼容旧版 mb 配置（如果启用了 mb）
        if self.mb and self.mb.enabled:
            # 将旧版 mb 配置转换为 cabinet 格式
            return CabinetConfig(
                type=self.mb.type,
                mb_path=self.mb.mb_path,
                ter_id=self.mb.ter_id,
                port=self.mb.port,
                user=self.mb.user
            )
        # 默认返回 mock
        return CabinetConfig(type="mock")


def _load_payments_config(get_value) -> dict:
    """加载制单配置（含批量文件解析）"""
    batch_file = get_value('collection.payments', 'batch_file', '')
    payment_items = []
    if batch_file and Path(batch_file).exists():
        try:
            suffix = Path(batch_file).suffix.lower()
            with open(batch_file, 'r', encoding='utf-8-sig') as f:
                if suffix == '.json':
                    raw_items = json.load(f)
                else:
                    import csv
                    reader = csv.DictReader(f)
                    raw_items = list(reader)
            for item in raw_items:
                payment_items.append({k: str(v) for k, v in item.items()})
        except Exception:
            payment_items = []

    return {
        'enabled': get_value('collection.payments', 'enabled', False, bool),
        'action': get_value('collection.payments', 'action', 'save'),
        'batch_file': batch_file,
        'template_file': get_value('collection.payments', 'template_file', ''),
        'template_mapping_file': get_value('collection.payments', 'template_mapping_file', ''),
        'generated_file': get_value('collection.payments', 'generated_file', ''),
        'items': payment_items,
        'payer_account': get_value('collection.payments', 'payer_account', ''),
        'payer_name': get_value('collection.payments', 'payer_name', ''),
        'amount': get_value('collection.payments', 'amount', ''),
        'currency': get_value('collection.payments', 'currency', '人民币'),
        'payee_account': get_value('collection.payments', 'payee_account', ''),
        'payee_name': get_value('collection.payments', 'payee_name', ''),
        'payee_account_type': get_value('collection.payments', 'payee_account_type', ''),
        'payee_bank': get_value('collection.payments', 'payee_bank', ''),
        'payee_branch': get_value('collection.payments', 'payee_branch', ''),
        'payee_bank_code': get_value('collection.payments', 'payee_bank_code', ''),
        'payee_bank_type': get_value('collection.payments', 'payee_bank_type', '02'),
        'purpose': get_value('collection.payments', 'purpose', ''),
        'remark': get_value('collection.payments', 'remark', ''),
        'reconcile_no': get_value('collection.payments', 'reconcile_no', ''),
        'business_flow': get_value('collection.payments', 'business_flow', ''),
        'payment_method': get_value('collection.payments', 'payment_method', ''),
        'serial_no': get_value('collection.payments', 'serial_no', ''),
        'notify': get_value('collection.payments', 'notify', '0'),
        'payee_phone': get_value('collection.payments', 'payee_phone', ''),
        'notify_msg': get_value('collection.payments', 'notify_msg', ''),
        'batch_name': get_value('collection.payments', 'batch_name', ''),
        'verify_name': get_value('collection.payments', 'verify_name', False, bool),
        'prefer_realtime': get_value('collection.payments', 'prefer_realtime', False, bool),
    }


def _load_offline_import_config(get_value) -> dict:
    """加载离线明细导入配置"""
    return {
        'enabled': get_value('collection.offline_import', 'enabled', False, bool),
        'source_directory': get_value('collection.offline_import', 'source_directory', ''),
        'filename_pattern': get_value('collection.offline_import', 'filename_pattern', '离线账户明细导入_已填_*.xls'),
        'click_recognize': get_value('collection.offline_import', 'click_recognize', True, bool),
        'wait_recognize_success': get_value('collection.offline_import', 'wait_recognize_success', True, bool),
        'click_submit': get_value('collection.offline_import', 'click_submit', True, bool),
        'recognize_timeout': get_value('collection.offline_import', 'recognize_timeout', 30, int),
        'recognize_refresh_interval': get_value('collection.offline_import', 'recognize_refresh_interval', 3, int),
        'upload_timeout': get_value('collection.offline_import', 'upload_timeout', 30, int),
    }


def _load_ini_config(config_path: Path) -> dict:
    """从 INI 文件加载配置"""
    import configparser
    
    parser = configparser.ConfigParser()
    parser.read(config_path, encoding='utf-8-sig')
    
    def get_value(section, key, default=None, value_type=str):
        """获取配置值并转换类型"""
        try:
            value = parser.get(section, key, fallback=default)
            if value is None:
                return default
            if isinstance(value, str):
                # ConfigParser 默认不会处理行内注释；只剥离空白后的 ;/# 注释，避免影响密码等实际值。
                value = re.split(r'\s+[;#]', value, maxsplit=1)[0].strip()
            if value_type == bool:
                return value.lower() in ('true', 'yes', '1', 'on')
            elif value_type == int:
                return int(value)
            elif value_type == list:
                # 逗号分隔的列表
                return [x.strip() for x in value.split(',') if x.strip()] if value else []
            return value
        except Exception:
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
                'file_format': get_value('collection.receipts', 'file_format', 'pdf'),
                'download_path': get_value('collection.receipts', 'download_path', './downloads/receipts'),
                'organize_by_date': get_value('collection.receipts', 'organize_by_date', True, bool),
                'archive_by_month': get_value('collection.receipts', 'archive_by_month', True, bool),
                'retention_months': get_value('collection.receipts', 'retention_months', 2, int),
            },
            'reconciliation': {
                'enabled': get_value('collection.reconciliation', 'enabled', True, bool),
                'days_back': get_value('collection.reconciliation', 'days_back', 30, int),
                'start_date': get_value('collection.reconciliation', 'start_date', ''),
                'end_date': get_value('collection.reconciliation', 'end_date', ''),
                'accounts': get_value('collection.reconciliation', 'accounts', [], list),
                'file_format': get_value('collection.reconciliation', 'file_format', 'csv'),
                'download_path': get_value('collection.reconciliation', 'download_path', './downloads/reconciliation'),
                'archive_by_month': get_value('collection.reconciliation', 'archive_by_month', True, bool),
                'retention_months': get_value('collection.reconciliation', 'retention_months', 2, int),
            },
            'payments': _load_payments_config(get_value),
            'offline_import': _load_offline_import_config(get_value),
        },
        'browser': {
            'login_url': get_value('browser', 'login_url', ''),
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
        'captcha': {
            'enabled': get_value('captcha', 'enabled', False, bool),
            'api_key': get_value('captcha', 'api_key', ''),
            'secret_key': get_value('captcha', 'secret_key', ''),
            'dialog_selector': get_value('captcha', 'dialog_selector', ''),
            'input_selector': get_value('captcha', 'input_selector', ''),
            'img_selector': get_value('captcha', 'img_selector', ''),
            'submit_selector': get_value('captcha', 'submit_selector', ''),
        },
        'mb': {
            'enabled': get_value('mb', 'enabled', False, bool),
            'type': get_value('mb', 'type', 'mb'),
            'mb_path': get_value('mb', 'mb_path', 'mb'),
            'ter_id': get_value('mb', 'ter_id', ''),
            'port': get_value('mb', 'port', 1, int),
            'user': get_value('mb', 'user', 'test'),
        },
        'cabinet': {
            'type': get_value('cabinet', 'type', 'mb'),
            'manual_mode': get_value('cabinet', 'manual_mode', False, bool),
            'mb_path': get_value('cabinet', 'mb_path', 'mb'),
            'ter_id': get_value('cabinet', 'ter_id', ''),
            'port': get_value('cabinet', 'port', 1, int),
            'user': get_value('cabinet', 'user', 'test'),
            'gf3_path': get_value('cabinet', 'gf3_path', 'gf3.exe'),
            'product_id': get_value('cabinet', 'product_id', 1, int),
            'api_url': get_value('cabinet', 'api_url', ''),
            'api_key': get_value('cabinet', 'api_key', ''),
            'device_id': get_value('cabinet', 'device_id', ''),
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
        with open(config_file, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
    elif suffix in ['.yaml', '.yml']:
        with open(config_file, 'r', encoding='utf-8-sig') as f:
            data = yaml.safe_load(f)
    else:
        # 尝试自动检测
        with open(config_file, 'r', encoding='utf-8-sig') as f:
            content = f.read()
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                try:
                    data = yaml.safe_load(content)
                except Exception:
                    # 尝试 INI 格式
                    data = _load_ini_config(config_file)
    
    # 处理 payments 配置（兼容 JSON/YAML 直接传入的情况）
    payments_data = data.get('collection', {}).get('payments', {})
    if payments_data and 'items' in payments_data and isinstance(payments_data['items'], list):
        items_raw = payments_data.pop('items', [])
        payments_data['items'] = [PaymentItem(**item) for item in items_raw if isinstance(item, dict)]
    else:
        payments_data['items'] = []

    return Config(
        credentials=CredentialsConfig(**data.get('credentials', {})),
        collection=CollectionConfig(
            statements=StatementsConfig(**data.get('collection', {}).get('statements', {})),
            receipts=ReceiptsConfig(**data.get('collection', {}).get('receipts', {})),
            reconciliation=ReconciliationConfig(**data.get('collection', {}).get('reconciliation', {})),
            payments=PaymentsConfig(**payments_data),
            offline_import=OfflineImportConfig(**data.get('collection', {}).get('offline_import', {}))
        ),
        browser=BrowserConfig(**data.get('browser', {})),
        runtime=RuntimeConfig(**data.get('runtime', {})),
        schedule=ScheduleConfig(**data.get('schedule', {})),
        captcha=CaptchaConfig(**data.get('captcha', {})),
        mb=MBConfig(**data.get('mb', {})),
        cabinet=CabinetConfig(**data.get('cabinet', {}))
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
        'file_format': rcpt.file_format or 'pdf',
        'download_path': rcpt.download_path or './downloads/receipts',
        'organize_by_date': str(rcpt.organize_by_date).lower(),
        'archive_by_month': str(rcpt.archive_by_month).lower(),
        'retention_months': str(rcpt.retention_months),
    }

    # collection.reconciliation 部分
    recon = config.collection.reconciliation
    parser['collection.reconciliation'] = {
        'enabled': str(recon.enabled).lower(),
        'days_back': str(recon.days_back),
        'start_date': recon.start_date or '',
        'end_date': recon.end_date or '',
        'accounts': ','.join(recon.accounts) if recon.accounts else '',
        'file_format': recon.file_format or 'csv',
        'download_path': recon.download_path or './downloads/reconciliation',
        'archive_by_month': str(recon.archive_by_month).lower(),
        'retention_months': str(recon.retention_months),
    }

    # collection.payments 部分
    pay = config.collection.payments
    parser['collection.payments'] = {
        'enabled': str(pay.enabled).lower(),
        'action': pay.action or 'save',
        'batch_file': pay.batch_file or '',
        'template_file': pay.template_file or '',
        'template_mapping_file': pay.template_mapping_file or '',
        'generated_file': pay.generated_file or '',
        'payer_account': pay.payer_account or '',
        'payer_name': pay.payer_name or '',
        'amount': pay.amount or '',
        'currency': pay.currency or '人民币',
        'payee_account': pay.payee_account or '',
        'payee_name': pay.payee_name or '',
        'payee_account_type': pay.payee_account_type or '',
        'payee_bank': pay.payee_bank or '',
        'payee_branch': pay.payee_branch or '',
        'payee_bank_code': pay.payee_bank_code or '',
        'payee_bank_type': pay.payee_bank_type or '02',
        'purpose': pay.purpose or '',
        'remark': pay.remark or '',
        'reconcile_no': pay.reconcile_no or '',
        'business_flow': pay.business_flow or '',
        'payment_method': pay.payment_method or '',
        'serial_no': pay.serial_no or '',
        'notify': pay.notify or '0',
        'payee_phone': pay.payee_phone or '',
        'notify_msg': pay.notify_msg or '',
        'batch_name': pay.batch_name or '',
        'verify_name': str(pay.verify_name).lower(),
        'prefer_realtime': str(pay.prefer_realtime).lower(),
    }

    # collection.offline_import 部分
    oi = config.collection.offline_import
    parser['collection.offline_import'] = {
        'enabled': str(oi.enabled).lower(),
        'source_directory': oi.source_directory or '',
        'filename_pattern': oi.filename_pattern or '离线账户明细导入_已填_*.xls',
        'click_recognize': str(oi.click_recognize).lower(),
        'wait_recognize_success': str(oi.wait_recognize_success).lower(),
        'click_submit': str(oi.click_submit).lower(),
        'recognize_timeout': str(oi.recognize_timeout),
        'recognize_refresh_interval': str(oi.recognize_refresh_interval),
        'upload_timeout': str(oi.upload_timeout),
    }

    # browser 部分
    br = config.browser
    parser['browser'] = {
        'login_url': br.login_url or '',
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
    
    # captcha 部分
    cap = config.captcha
    parser['captcha'] = {
        'enabled': str(cap.enabled).lower(),
        'api_key': cap.api_key or '',
        'secret_key': cap.secret_key or '',
        'dialog_selector': cap.dialog_selector or '',
        'input_selector': cap.input_selector or '',
        'img_selector': cap.img_selector or '',
        'submit_selector': cap.submit_selector or '',
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
