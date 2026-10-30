# 中信银行企业网银自动化工具

用于中信银行企业网银的自动化操作，目前主要维护两类能力：

- 银行流水、电子回单采集
- 批量转账制单，通过银行批量模板导入完成

当前制单流程已经改为“批量模板导入”为主。日常付款数据维护在 JSON 文件里，程序会自动生成银行要求的 Excel 导入文件，再上传到企业网银批量转账页面。

## 目录结构

```text
中信网银/
├── README.md
└── citic_automation/
    ├── main.py                         # 程序入口
    ├── config.ini                      # 主配置文件
    ├── payment_creator.py              # 批量转账制单逻辑
    ├── browser.py                      # 中信网银浏览器适配
    ├── statement_collector.py          # 银行流水采集
    ├── receipt_collector.py            # 电子回单采集
    ├── temp/
    │   ├── batch_payments.json         # 日常维护的批量付款数据
    │   └── payment_import.xlsx         # 程序生成的银行导入文件
    ├── template/
    │   ├── 批量转账模板.xlsx           # 银行批量转账模板
    │   └── payments_batch_template.json
    ├── records/                        # 运行记录、执行结果截图
    ├── debug/                          # 提交前调试截图和 HTML
    ├── logs/                           # 日志
    └── requirements.txt
```

## 日常使用

进入程序目录：

```bash
cd c:\wangyingbao\中信网银\citic_automation
```

只执行制单：

```bash
python main.py -p
```

如果配置里同时启用了流水或回单采集，也可以直接运行：

```bash
python main.py
```

## 批量转账制单

### 1. 配置入口

制单配置在 `citic_automation/config.ini` 的 `[collection.payments]`：

```ini
[collection.payments]
enabled = true
action = submit
batch_file = temp/batch_payments.json
template_file = template/批量转账模板.xlsx
generated_file = temp/payment_import.xlsx

payer_account = 付款账号/户名/币种
currency = 人民币
business_flow = 审批流程
payment_method = 跨行转账
```

说明：

- `enabled = true`：启用制单。
- `action = submit`：上传后直接提交；如需人工核对，可改为 `save`，程序会停在提交前。
- `batch_file`：日常维护的付款 JSON，默认使用 `temp/batch_payments.json`。
- `template_file`：银行批量转账模板。
- `generated_file`：程序生成后上传到网银的 Excel 文件。
- `payer_account`、`currency`、`business_flow`、`payment_method`：作为批量付款的公共默认值。

### 2. 维护付款 JSON

日常只需要编辑：

```text
citic_automation/temp/batch_payments.json
```

示例：

```json
[
  {
    "payee_account": "50131000914000181",
    "payee_name": "上海某某科技有限公司",
    "payee_account_type": "公司账户",
    "payee_bank": "农村商业银行",
    "payee_branch": "上海农村商业银行股份有限公司青浦支行",
    "payee_bank_code": "501310099",
    "amount": "72480",
    "purpose": "技术服务费",
    "remark": "2026年服务费-首期款"
  }
]
```

常用字段：

| 字段 | 说明 | 是否常用 |
| --- | --- | --- |
| `payee_account` | 收款账号 | 必填 |
| `payee_name` | 收款户名 | 必填 |
| `payee_branch` | 收款银行开户网点 | 必填 |
| `payee_bank_code` | 支付联行号 | 建议填写 |
| `amount` | 金额 | 必填 |
| `purpose` | 附言，写入导入模板逐笔附言列 | 可填 |
| `remark` | 备注，写入导入模板逐笔备注列 | 可填 |
| `payer_account` | 付款账号，不填则使用 `config.ini` | 可选 |
| `currency` | 币种，不填则使用 `config.ini` | 可选 |
| `payment_method` | 付款方式，不填则使用 `config.ini` | 可选 |
| `reconcile_no` | 对账编号 | 可选 |

注意：

- JSON 必须是合法格式，最后一条后面不要多写逗号。
- 金额可以写 `"100"` 或 `"100.00"`，程序会按两位小数写入模板。
- 批量页面底部的公共“附言”和“备注”不再填写，使用导入模板里的逐笔附言和逐笔备注。

### 3. 程序实际做什么

执行 `python main.py -p` 后，程序会：

1. 读取 `temp/batch_payments.json`。
2. 基于 `template/批量转账模板.xlsx` 生成 `temp/payment_import.xlsx`。
3. 打开中信企业网银批量转账页面。
4. 上传 `payment_import.xlsx`。
5. 填写总笔数、总金额、业务流程。
6. 提交制单。
7. 遇到 U 盾密码弹窗时自动输入配置里的 `pin`。
8. 提交成功后保存执行结果截图。

## 执行结果截图

制单提交成功后，程序会在结果弹窗关闭前截图，保存到：

```text
citic_automation/records/yyyy-mm-dd执行结果.png
```

例如：

```text
citic_automation/records/2026-04-29执行结果.png
```

这个截图可以直接用于告知用户本次制单结果。

## 浏览器配置

中信网银和 UKey 控件更适合使用本机安装的 Chrome。建议在 `config.ini` 中显式配置：

```ini
[browser]
headless = false
chrome_path = C:\Program Files\Google\Chrome\Application\chrome.exe
```

如果启动时报：

```text
Target page, context or browser has been closed
```

优先检查：

- `chrome_path` 是否指向真实存在的 Chrome。
- 是否已有异常残留 Chrome 进程。
- `citic_automation/chrome_user_data` 或 `common/chrome_user_data` 是否因上次异常关闭损坏。

## UKey 和机柜

登录和提交时会涉及 UKey：

- 登录 PIN 使用 `[credentials]` 里的 `pin`。
- 提交时如出现 U 盾密码窗口，程序会尝试自动输入并确认。
- 如果启用了 GF3 或 MB 机柜，程序会按 `[cabinet]` 配置控制 U 盾插拔和确认。

没有机柜时，可把机柜改为手动模式：

```ini
[cabinet]
manual_mode = true
```

## 安装依赖

```bash
cd c:\wangyingbao\中信网银\citic_automation
pip install -r requirements.txt
playwright install chromium
```

实际运行中建议配置系统 Chrome 路径，Playwright Chromium 主要作为基础依赖。

## 命令

```bash
# 执行全部启用任务
python main.py

# 只执行批量制单
python main.py -p

# 只采集流水
python main.py -s

# 只采集回单
python main.py -r

# 指定配置文件
python main.py -c custom_config.ini
```

## 常见问题

### 批量文件找不到

确认 `config.ini` 中：

```ini
batch_file = temp/batch_payments.json
```

如果 `batch_file` 留空，程序也会默认尝试读取 `temp/batch_payments.json`。

### 总金额填写异常

银行页面可能会把 `9010.00` 显示为 `9,010.00`。程序已按数值比较金额，正常情况下不会因为千分位逗号误判。

### 上传成功但底部附言/备注为空

这是当前设计。批量转账使用模板中的逐笔附言、逐笔备注，页面底部公共附言和备注不再自动填写。

### 没有看到结果截图

结果截图只有在提交后出现最终结果弹窗时保存。检查：

- `records/` 目录是否存在当天的 `yyyy-mm-dd执行结果.png`。
- 日志里是否有“已保存执行结果截图”。
- 是否停在提交前或提交过程中失败。

## 日志和调试文件

- 日志：`citic_automation/logs/citic_automation_YYYYMMDD.log`
- 提交前截图：`citic_automation/debug/payment_before_action_*.png`
- 提交前 HTML：`citic_automation/debug/payment_before_action_*.html`
- 执行结果截图：`citic_automation/records/yyyy-mm-dd执行结果.png`

## 更新记录

### 2026-04-29

- 制单流程调整为批量模板导入为主。
- 默认使用 `temp/batch_payments.json` 维护批量付款数据。
- 生成并上传 `temp/payment_import.xlsx`。
- 批量页不再填写底部公共附言和备注。
- 提交成功后自动保存 `yyyy-mm-dd执行结果.png`。
- 推荐显式使用系统 Chrome 路径运行中信网银。

### 2026-04-24

- 新增制单能力。
- 新增 `-p` / `--payments-only` 参数。

### 2026-04-17

- 抽取浏览器基础能力到公共模块。
- 支持 MB / GF3 机柜控制。

### 2025-02-11

- 初始版本。
- 支持 UKey 登录、流水采集、回单采集。
