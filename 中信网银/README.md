# 中信银行企业网银自动采集工具

基于 Playwright 的中信银行企业网银自动化采集程序，支持银行流水和电子回单的自动下载。

## 特性

- **UKey证书登录**: 支持中信银行UKey数字证书登录
- **自动PIN输入**: 支持自动输入UKey PIN码（通过虚拟键盘）
- **流水采集**: 自动下载银行交易明细
- **电子回单采集**: 自动下载电子回单
- **灵活配置**: 支持日期范围、账户选择等配置

## 目录结构

```
中信网银/
├── citic_automation/
│   ├── browser.py              # 浏览器自动化模块
│   ├── config_loader.py        # 配置加载模块
│   ├── config.yaml             # 配置文件
│   ├── logger.py               # 日志模块
│   ├── main.py                 # 主程序入口
│   ├── print_handler.py        # 打印对话框处理
│   ├── receipt_collector.py    # 电子回单采集模块
│   ├── requirements.txt        # 依赖列表
│   ├── statement_collector.py  # 银行流水采集模块
│   └── .gitignore              # Git忽略配置
└── README.md                   # 说明文档
```

## 安装

### 1. 安装依赖

```bash
cd citic_automation
pip install -r requirements.txt
```

### 2. 安装 Playwright 浏览器

```bash
playwright install chromium
```

### 3. 配置

程序支持 **JSON** 和 **YAML** 两种格式的配置文件，优先查找 `config.json`，其次是 `config.yaml`。

推荐使用 **JSON 格式**，便于程序动态修改配置（如定时任务场景）：

```json
{
  "credentials": {
    "username": "您的用户名",
    "password": "您的登录密码",
    "pin": "您的UKey PIN码"
  },
  "collection": {
    "statements": {
      "enabled": true,
      "days_back": 30,
      "start_date": "2026-01-01",
      "end_date": "2026-02-26",
      "file_format": "excel"
    },
    "receipts": {
      "enabled": true,
      "days_back": 30
    }
  }
}
```

**关键配置项说明：**
- `collection.statements.enabled`: 是否采集流水 (`true`/`false`)
- `collection.receipts.enabled`: 是否采集回单 (`true`/`false`)
- `credentials.pin`: UKey PIN码（用于自动输入）

## 使用方法

### 基本用法

```bash
# 采集流水和回单（默认）
python main.py

# 仅采集流水
python main.py -s

# 仅采集回单
python main.py -r

# 使用自定义配置文件
python main.py -c custom_config.yaml
```

### 使用步骤

1. **插入UKey**: 运行程序前，请确保中信银行UKey已插入电脑

2. **运行程序**:
   ```bash
   python main.py
   ```

3. **自动/手动登录**:
   - 如果配置了PIN码，程序会尝试自动输入
   - 如果自动登录失败，程序会等待您手动完成登录
   - 请在浏览器中选择证书并输入UKey密码

4. **自动采集**:
   - 登录成功后，程序会自动采集流水和回单
   - 下载的文件会保存在配置的目录中

## 配置文件说明

### 登录凭证 (credentials)

| 字段 | 说明 | 示例 |
|------|------|------|
| username | 操作员代码 | "admin" |
| password | 登录密码 | "your_password" |
| pin | UKey PIN码 | "123456" |

### 采集设置 (collection)

**银行流水 (statements)**

| 字段 | 说明 | 默认值 |
|------|------|--------|
| days_back | 查询天数（当start_date为空时生效） | 30 |
| start_date | 开始日期（YYYY-MM-DD） | "" |
| end_date | 结束日期（YYYY-MM-DD） | "" |
| file_format | 文件格式: excel, pdf, csv | "excel" |
| download_path | 下载保存路径 | "./downloads/statements" |

**电子回单 (receipts)**

| 字段 | 说明 | 默认值 |
|------|------|--------|
| days_back | 查询天数 | 30 |
| download_path | 下载保存路径 | "./downloads/receipts" |

### 浏览器设置 (browser)

| 字段 | 说明 | 默认值 |
|------|------|--------|
| login_url | 登录页面URL | "https://corp.bank.ecitic.com/cotb/login.html" |
| headless | 无头模式（后台运行） | false |
| page_timeout | 页面操作超时（秒） | 30 |

## 注意事项

1. **UKey驱动**: 确保中信银行UKey驱动已正确安装

2. **浏览器兼容性**: 推荐使用Chrome或Edge浏览器

3. **安全控件**: 如果需要中信银行的安全控件扩展，请将.crx文件放在项目根目录，并在配置中指定路径

4. **分辨率**: 建议屏幕分辨率为1920x1080或更高，以确保页面元素正常显示

5. **PIN码输入**: 
   - 如果配置了PIN码，程序会尝试自动输入
   - 自动输入使用pyautogui模拟键盘输入
   - 如果自动输入失败，可以手动输入

6. **下载文件**:
   - 流水文件命名格式: `中信银行_银行流水_yyyymmdd.xlsx`
   - 回单文件命名格式: `中信银行_电子回单_yyyymmdd.pdf`

## 故障排除

### 无法启动浏览器

- 检查Chrome或Edge是否已安装
- 运行 `playwright install chromium` 安装浏览器

### 无法检测到登录成功

- 检查登录凭据是否正确
- 尝试手动登录看是否成功
- 检查网络连接是否正常

### 无法下载文件

- 检查下载目录是否有写入权限
- 确认当前查询条件下有数据
- 查看日志文件了解详细错误信息

### PIN码输入失败

- 确保已安装 `pyautogui`: `pip install pyautogui`
- 确保浏览器窗口在前台
- 尝试手动输入PIN码

## 日志

日志文件保存在 `logs/citic_automation_YYYYMMDD.log`，包含详细的运行信息，可用于故障排查。

## 技术栈

- **Playwright**: 浏览器自动化
- **PyYAML**: 配置文件解析
- **Loguru**: 日志记录
- **PyAutoGUI**: 屏幕自动化（用于PIN输入）

## 许可证

内部使用工具，请勿外传。

## 更新记录

### v1.0.0 (2025-02-11)
- 初始版本
- 支持UKey证书登录
- 支持银行流水采集
- 支持电子回单采集
- 支持自动PIN输入
