# 建设银行企业网银采集与制单

Playwright 驱动建行企业网银，支持银行流水、电子回单采集和批量转账制单。平台入口为 `run_task.py`（采集）和 `run_draft.py`（制单），接入契约见 `platform/docs/bank-collector-contract.md`。

## 运行

```bash
cd c:\wangyingbao\建设银行\ccb_automation
python main.py      # 按 config.ini 启用的模块执行
python main.py -s   # 仅流水
python main.py -r   # 仅回单
python main.py -p   # 仅制单
python main.py -c custom.ini
```

平台调用：`python run_task.py --task <task.json>`、`python run_draft.py --task <task.json>`。

## 配置

`config.ini`（本机配置，不入库）：

- `[credentials]`：用户名、密码、UKey PIN。
- `[collection.statements]` / `[collection.receipts]`：开关、查询天数、下载目录。
- `[collection.payments]`：`enabled`、`action`（`save` 停在“下一步”前供人工核对，`submit` 自动提交）、`batch_file`、`template_file`、`batch_name`、`verify_name`、`prefer_realtime`。
- `[browser]`：登录地址 `https://b2b.ccb.com/NCCB/V6/b2bindex.jsp?PT_LANGUAGE=CN`。
- `[cabinet]`：机柜类型与端口。

## 制单

- 付款清单在 `temp/batch_payments.json`，每笔至少填 `payee_account`、`payee_name`、`payee_branch`、`amount`，可选 `purpose`、`remark`、`payee_bank_code` 等。
- `payment_creator.py` 按 `template/批量转账模板.xls` 生成导入文件（Sheet1 第 2 行起），列映射在 `template/payments_batch_template.json`。
- 提交前会自动截图到 `debug/`，便于核对表单。

## 页面与坑

- U 盾密码可能弹 Windows 原生窗口，`pin_inputter.py` 用 AiKeyboard 处理。
- 银行页面会把 `9010.00` 显示为 `9,010.00`，金额比较按数值处理。
- 批量文件里的附言/备注写入模板逐笔列，页面底部公共附言不再自动填。
- 日志在 `logs/`，下载文件在 `downloads/`。

## 已知限制

- `submit` 模式的后续确认页结构可能随银行版本变化，建议先用 `save` 观察后再开自动提交。
- 缺少 `xlrd/xlwt/xlutils` 时无法处理 `.xls` 模板，安装依赖即可。
- 模板文件被占用（Excel 打开着）会导致生成失败。
