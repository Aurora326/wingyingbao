# 中信银行企业网银采集

Playwright 驱动中信企业网银，采集银行流水和电子回单。平台入口为 `run_task.py`，接入契约见 `platform/docs/bank-collector-contract.md`。

## 运行

```bash
cd c:\wangyingbao\中信网银\citic_automation
pip install -r requirements.txt
playwright install chromium

python main.py        # 流水 + 回单
python main.py -s     # 仅流水
python main.py -r     # 仅回单
python main.py -c custom.ini
```

## 配置

`config.ini`（本机配置，不入库）：

- `[credentials]`：用户名、密码、UKey PIN。平台任务会用 `BANK_*` 环境变量覆盖。
- `[collection.statements]` / `[collection.receipts]`：开关、查询天数、下载目录。
- `[browser]`：登录地址 `https://corp.bank.ecitic.com/cotb/login.html`；建议显式配置本机 Chrome 路径，UKey 控件在系统 Chrome 上更稳定。
- `[cabinet]`：机柜类型（mb / gf3 / mock）与端口。

## 页面与坑

- 登录涉及 UKey 证书选择弹窗，`popup_handler.py` 负责自动确认。
- 金额比较按数值处理，页面的千分位逗号（`9,010.00`）不会误判。
- 上传/下载优先用 Playwright 的 `expect_download`，失败时回退到目录轮询。
- 下载文件在 `downloads/`，日志在 `logs/citic_automation_YYYYMMDD.log`。

## 已知限制

- `payment_creator.py`（批量转账制单）是早期实现，当前 `main.py` 和 `run_task.py` 都没有接入，目录里的 `batch_payments.json`、模板映射不生效。需要制单时再按建行/上海银行的形态接入。
- 页面改版时选择器需要重取，主要集中在 `login_manager.py` 和两个 collector。
