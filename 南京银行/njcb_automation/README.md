# 南京银行企业网银自动采集工具

## 功能说明

- 自动登录南京银行企业网银
- 自动采集银行流水
- 自动采集电子对账单
- 自动采集电子回单
- 支持 UKey 自动 PIN 码输入
- 支持机柜自动插拔 U 盾（MB/GF3）
- 支持按年月归档下载文件

## 目录结构

```
njcb_automation/
├── main.py                     # 主程序入口
├── browser.py                  # 浏览器管理
├── login_manager.py            # 登录逻辑
├── popup_handler.py            # 弹窗处理
├── pin_inputter.py             # PIN 自动输入
├── statement_collector.py      # 流水采集
├── receipt_collector.py        # 电子回单采集
├── reconciliation_collector.py # 电子对账单采集
├── config_loader.py            # 配置加载（引用 common）
├── logger.py                   # 日志配置（引用 common）
├── cabinet_controller.py       # 机柜控制（引用 common）
├── run_recorder.py             # 运行记录（引用 common）
└── config.ini                  # 配置文件
```

## 使用方法

### 1. 安装依赖

运行 `安装依赖.bat` 或手动执行：

```bash
pip install playwright loguru pyyaml pyautogui
python -m playwright install chromium
```

### 2. 配置参数

编辑 `njcb_automation/config.ini`：

```ini
[credentials]
username = 你的用户名
password = 你的登录密码
pin = 你的UKey PIN码

[browser]
login_url = https://corpauth.njcb.com.cn/ccsp-sso-web/#/login?client_id=ctms-client

[cabinet]
type = mock       ; mock=手动模式, mb=MB机柜, gf3=GF3机柜
manual_mode = true

[collection.statements]
enabled = true
days_back = 7
start_date = 2026-07-06
end_date = 2026-07-06
accounts = 
file_format = excel
download_path = ./downloads/statements

[collection.reconciliation]
enabled = true
days_back = 7
start_date = 2026-07-06
end_date = 2026-07-06
accounts = 
file_format = csv
download_path = ./downloads/reconciliation

[collection.receipts]
enabled = true
days_back = 30
accounts = 
file_format = pdf
download_path = ./downloads/receipts
```

### 3. 启动程序

```bash
cd njcb_automation
python main.py
```

或双击 `启动采集程序.bat`

命令行参数：

```bash
python main.py              # 采集流水、电子对账单、电子回单
python main.py -s           # 仅采集流水
python main.py -rec         # 仅采集电子对账单
python main.py -r           # 仅采集电子回单
```

## 注意事项

1. 首次运行建议设置 `headless = false` 以观察浏览器操作
2. 如果页面元素选择器与实际不符，请根据实际情况修改代码中的选择器
3. 流水查询页面 URL 为：`https://ntms.njcb.com.cn/account/bankAccountManage/query/bankAccFlowQuery`
4. 电子对账单查询页面 URL 为：`https://ntms.njcb.com.cn/pay/bankReconciliation`
5. 日志文件保存在 `logs/` 目录下

## 常见问题

- **登录失败**：检查用户名、密码、PIN 码是否正确
- **元素找不到**：页面可能有更新，需要调整 CSS/XPath 选择器
- **下载失败**：检查下载目录权限和磁盘空间
- **电子对账单无数据**：确认所选账号在指定日期范围内是否有对账单
