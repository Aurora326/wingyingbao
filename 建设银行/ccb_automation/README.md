# 建设银行企业网银自动化工具

基于 Playwright 的建设银行企业网银自动采集与批量制单工具，支持银行流水采集、电子回单采集、批量转账制单。

---

## 功能说明

| 功能 | 说明 |
|------|------|
| **银行流水采集** | 自动登录后进入交易明细页面，按日期范围查询并下载 Excel 格式流水 |
| **电子回单采集** | 从交易明细结果页全选记录，预览并生成 PDF 电子回单 |
| **批量制单** | 读取 JSON/CSV 付款清单，生成建行标准 `.xls` 批量转账模板，自动上传并提交 |

---

## 环境要求

- Python 3.9+
- Windows 系统（依赖 Win32 API 处理弹窗和 U 盾）
- Chrome / Edge 浏览器
- 建设银行网银盾（UKey）及驱动已安装

---

## 安装依赖

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器
playwright install chromium
```

---

## 配置文件（config.ini）

所有配置集中在 `config.ini`，各段说明如下：

### [credentials] — 登录凭据

```ini
[credentials]
username = SH31000009241071101    ; 操作员代码
password = WD123456               ; 登录密码
pin = WD123456                    ; 网银盾（UKey）PIN 码
```

> 建设银行支持证书登录（UKey）和操作员代码登录两种方式，建议优先使用 UKey 登录。

### [collection.statements] — 流水采集

```ini
[collection.statements]
enabled = true                    ; 是否启用
days_back = 90                    ; 查询最近 N 天
file_format = xlsx                ; 下载格式
download_path = ./downloads/statements
archive_by_month = true           ; 按年月归档
retention_months = 2              ; 保留最近 2 个月
```

### [collection.receipts] — 电子回单采集

```ini
[collection.receipts]
enabled = true
days_back = 90
download_path = ./downloads/receipts
archive_by_month = true
retention_months = 2
```

### [collection.payments] — 批量制单

```ini
[collection.payments]
enabled = false                   ; 是否启用制单
action = save                     ; save=停在"下一步"前供人工核对; submit=自动提交
batch_file = temp/batch_payments.json      ; 付款数据文件
template_file = template/批量转账模板.xls   ; 银行官方模板
template_mapping_file = template/payments_batch_template.json
generated_file = temp/payment_import.xls    ; 自动生成的导入文件

; 默认付款账户（所有批量付款共用）
payer_account = 31001000000000000000
payer_name = 上海示例公司
payee_bank_type = 02              ; 01=本行, 02=国内他行
purpose = 货款
remark = 
notify = 0                        ; 0=不短信通知, 1=通知

; 页面表单选项
batch_name = 批量转账_20260507
verify_name = false               ; 行内收款账户户名校验
prefer_realtime = false           ; 优先采用跨行实时转账
```

### [cabinet] — 机柜控制器（可选）

```ini
[cabinet]
type = mb                         ; mb / gf3 / mock
manual_mode = false
port = 3
mb_path = C:\waicai\wangyingbao\m2\mb.exe
ter_id = 068330001533
user = 15711668178
```

---

## 批量制单使用说明

### 1. 准备付款数据

编辑 `temp/batch_payments.json`，格式为 JSON 数组：

```json
[
  {
    "payee_account": "43001570000059031234",
    "payee_name": "收款公司A",
    "amount": "1000.50",
    "purpose": "货款",
    "remark": "第一批"
  },
  {
    "payee_account": "6222001234567890123",
    "payee_name": "收款公司B",
    "amount": "2000.00",
    "payee_bank_type": "01",
    "purpose": "服务费"
  }
]
```

**字段说明：**

| 字段 | 必填 | 说明 |
|------|------|------|
| `payee_account` | 是 | 收款方客户账号 |
| `payee_name` | 是 | 收款方账户名称 |
| `amount` | 是 | 转账金额 |
| `purpose` | 是 | 用途/附言 |
| `payee_bank_type` | 否 | 01=本行，02=国内他行。默认取 `config.ini` 中的配置 |
| `payee_branch` | 否 | 收款方开户行名称 |
| `payee_bank_code` | 否 | 收款方联行号（大额跨行需要） |
| `remark` | 否 | 备注 |
| `serial_no` | 否 | 客户方流水号 |
| `notify` | 否 | 0=不短信通知，1=通知 |
| `payee_phone` | 否 | 收款人手机号码（短信通知用） |

> 付款方信息（`payer_account`、`payer_name` 等）如不写在每条数据中，会自动使用 `config.ini` 里的默认值。

### 2. 模板格式

建设银行官方批量转账模板为 `.xls` 格式，**Sheet1** 表头如下：

| 列 | 字段名 | 说明 |
|--:|--------|------|
| 1 | 序号 | 自动生成 |
| 2 | 付款方客户账号 | |
| 3 | 付款方账户名称 | |
| 4 | 收款方行别代码 | 01=本行，02=国内他行 |
| 5 | 收款方客户账号 | |
| 6 | 收款方账户名称 | |
| 7 | 收款方开户行名称 | |
| 8 | 收款方联行号 | |
| 9 | 客户方流水号 | |
| 10 | 金额 | |
| 11 | 用途 | |
| 12 | 备注 | |
| 13 | 是否短信通知 | 0=不通知，1=通知 |
| 14 | 收款人手机号码 | |
| 15 | 短信通知附加信息 | |

如需自定义列映射，修改 `template/payments_batch_template.json`。

---

## 运行命令

```bash
# 采集流水 + 回单 + 制单（按 config.ini 启用的模块执行）
python main.py

# 仅采集银行流水
python main.py -s

# 仅采集电子回单
python main.py -r

# 仅执行批量制单
python main.py -p

# 使用自定义配置文件
python main.py -c custom_config.ini
```

---

## 目录结构

```
ccb_automation/
├── main.py                          # 主程序入口
├── browser.py                       # 浏览器管理器
├── login_manager.py                 # 登录逻辑
├── popup_handler.py                 # 弹窗处理
├── pin_inputter.py                  # PIN 自动输入
├── payment_creator.py               # 批量制单模块
├── statement_collector.py           # 流水采集
├── receipt_collector.py             # 回单采集
├── config.ini                       # 主配置文件
├── requirements.txt                 # 依赖列表
├── README.md                        # 本文件
│
├── template/
│   ├── 批量转账模板.xls              # 银行官方模板（勿删）
│   └── payments_batch_template.json # 模板字段映射
│
├── temp/
│   └── batch_payments.json          # 付款数据示例
│
├── downloads/
│   ├── statements/                  # 流水下载目录
│   └── receipts/                    # 回单下载目录
│
├── logs/                            # 运行日志
├── debug/                           # 调试截图/HTML
└── records/                         # 运行记录
```

---

## 安全与调试

- **调试截图**：提交前会自动保存截图到 `debug/制单提交前.png`，方便核对表单状态
- **U 盾弹窗**：程序会自动检测并输入 PIN，支持 AiKeyboard（驱动级输入）和 pyautogui 双策略
- **机柜集成**：如配置 MB/GF3 机柜，程序会自动插拔 U 盾并按压物理 OK 键
- **弹窗兜底**：登录后多级弹窗清理（精确选择器 → 视口坐标 → DOM 暴力扫描）

---

## 常见问题

**Q1: 批量制单提示 "缺少 xlrd/xlwt/xlutils"？**
```bash
pip install xlrd xlwt xlutils
```

**Q2: `action=submit` 提交后没有反应？**
由于视频只展示到"下一步"页面，后续确认页面结构可能因银行版本不同而变化。建议先用 `action=save` 模式，观察后续页面后再决定是否开启自动提交。

**Q3: 如何只测试制单，不执行流水和回单？**
```bash
python main.py -p
```
或在 `config.ini` 中将 `collection.statements.enabled` 和 `collection.receipts.enabled` 设为 `false`。

**Q4: 付款数据能否用 CSV 格式？**
可以。将 `batch_file` 改为 `.csv` 路径即可，第一行为表头，字段名与 JSON 一致。

**Q5: 模板文件被占用导致生成失败？**
程序已做处理：通过临时文件中转，避免直接覆盖被占用的 `.xls` 文件。如仍失败，请关闭所有已打开的 Excel 进程后重试。

---

## 平台调用入口（run_task.py）

平台的任务通过 `run_task.py --task task.json` 执行，和工行、中信、兴业保持同一契约：

- 任务文件：`type`（statements/receipts）、`start_date`、`end_date`、`result_path`；
- 密钥：不写入任务文件，由环境变量 `BANK_USERNAME` / `BANK_PASSWORD` / `BANK_PIN` 传入；
- 结果：写入 `result_path` 的 JSON（`contract_version` / `ok` / `files` / `no_data` / `error_code` / `message`）；
- 制单：平台入口固定 `collect_payments=False`，不会触发 `action=submit`。需要制单仍走 `python main.py -p`。

采集程序清单里配置为：

```yaml
ccb:
  command: ["python", "run_task.py"]
  cwd: C:\wangyingbao\建设银行\ccb_automation
```

---

## 更新日志

| 日期 | 更新内容 |
|------|---------|
| 2026-05-07 | 新增批量制单功能（`payment_creator.py`），支持 `.xls` 模板导入、菜单导航、文件上传、U 盾弹窗处理 |
