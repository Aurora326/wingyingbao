# 工商银行企业网银采集

Playwright 驱动工行新一代企业网银，采集银行流水和电子回单。平台入口为 `run_task.py`，接入契约见 `platform/docs/bank-collector-contract.md`。

## 运行

```bash
cd c:\wangyingbao\工商银行\icbc_automation
python main.py        # 流水 + 回单
python main.py -s     # 仅流水
python main.py -r     # 仅回单
python main.py -c custom.ini
```

平台调用：`python run_task.py --task <task.json>`。

## 配置

`config.ini`（本机配置，不入库）：

- `[credentials]`：用户名、密码、U盾 PIN（工行必须）。平台任务用 `BANK_*` 环境变量覆盖。
- `[collection.statements]` / `[collection.receipts]`：开关、查询天数、下载目录。
- `[browser]`：登录地址 `https://corporbank-simp.icbc.com.cn/frame/web/index.html`。
- `[cabinet]`：机柜类型与端口；支持自动插拔 U 盾和点击物理 OK 键。

## 页面与坑

- 登录顺序：点「U盾登录」→ 证书弹窗选第一条 → 输 U 盾密码 → 等跳转成功。
- U 盾密码可能落在页面安全控件，也可能弹 Windows 原生窗口，`pin_inputter.py` 两种都处理；原生弹窗用 AiKeyboard 发送按键。
- 若 U 盾弹窗没有被自动处理，可把 `manual_mode` 打开，人工完成验证后再看采集步骤。
- 日志在 `logs/`，下载文件在 `downloads/`。

## 已知限制

- 页面改版后需要重取选择器，集中在 `login_manager.py` 和两个 collector。
- 没有制单能力。
