# 南京银行企业网银采集

Playwright 驱动南京银行企业网银，采集银行流水、电子回单和电子对账单，并附带一个把流水转换成宁波银行离线导入模板的工具。目前没有平台 `run_task.py` 入口，按本机脚本使用。

## 运行

```bash
cd c:\wangyingbao\南京银行\njcb_automation
python main.py          # 流水 + 回单
python main.py -s       # 仅流水
python main.py -r       # 仅回单
python main.py -rec     # 仅电子对账单
python main.py -c custom.ini
```

流水转换离线模板：

```bash
python convert_statement_to_template.py --input 南京银行_银行流水_20260703.xlsx --output 离线账户明细导入_已填_20260703.xls
```

## 配置

`config.ini`：

- `[credentials]`：用户名、密码、UKey PIN。
- `[collection.statements]` / `[collection.receipts]` / `[collection.reconciliation]`：各类采集开关与下载目录。
- `[browser]`：登录地址 `https://corpauth.njcb.com.cn/ccsp-sso-web/#/login?client_id=ctms-client`。
- `[captcha]`：验证码相关配置。
- `[cabinet]`：机柜类型与端口。

## 页面与坑

- 流水查询页：`https://ntms.njcb.com.cn/account/bankAccountManage/query/bankAccFlowQuery`
- 电子对账单页：`https://ntms.njcb.com.cn/pay/bankReconciliation`
- UKey PIN 由 `pin_inputter.py` 处理，安全控件场景走 AiKeyboard。
- 日志在 `logs/`，下载文件在 `downloads/`。

## 已知限制

- 没有 `run_task.py`，平台采集任务暂时调不到本程序；需要接入时按契约补一个入口。
- 页面选择器随银行改版需要重取。
