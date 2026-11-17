# 兴业银行企业网银采集

Playwright 驱动兴业企业网银，采集银行流水和电子回单，支持 UKey 证书登录。平台入口为 `run_task.py`，接入契约见 `platform/docs/bank-collector-contract.md`。

## 运行

```bash
cd c:\wangyingbao\兴业网银\cib_automation
python main.py        # 流水 + 回单
python main.py -s     # 仅流水
python main.py -r     # 仅回单
python main.py -c custom.yaml
```

首次使用先跑 `install_browser.bat` 和 `install_extension.py` 安装浏览器与 `CryptoKitExtension.CIB.crx` 扩展。

## 配置

`config.yaml`（本机配置，不入库）：

- `[credentials]`：用户名、密码、UKey PIN。平台任务用 `BANK_*` 环境变量覆盖。
- `[browser]`：登录地址 `https://corporatebank.cib.com.cn/firm/main/login/login`；`extension_path` 指向 `../CryptoKitExtension.CIB.crx`。
- `[cabinet]`：机柜类型与端口。

## 页面与坑

- 登录先尝试 UKey 证书登录，证书弹窗自动确认；UKey 需已插入并装好 CFCA 驱动。
- Playwright 浏览器下载慢时脚本有国内镜像回退，仍失败就单独跑 `install_browser.bat`。
- 下载文件在 `downloads/`，日志在 `logs/`。
- 目录里的 `debug_*.py`、`test_*.py` 是排障脚本，日常运行不需要。

## 已知限制

- 页面元素变动后需要重取选择器，主要在 `login_manager.py`、`statement_collector.py`、`receipt_collector.py`。
- 没有制单能力。
