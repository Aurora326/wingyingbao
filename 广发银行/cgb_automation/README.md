# 广发银行企业网银采集

Playwright 驱动广发企业网银，使用 Key盾/UKey 证书登录，采集银行流水和电子回单。目前没有平台 `run_task.py` 入口，按本机脚本使用。

## 运行

```bash
cd c:\wangyingbao\广发银行\cgb_automation
python main.py        # 流水 + 回单
python main.py -s     # 仅流水
python main.py -r     # 仅回单
python main.py -c custom.ini
```

打包版可执行文件在上一级 `发布版/` 目录。

## 配置

`config.ini`：

- `[credentials]`：用户名、密码、Key盾 PIN。
- `[collection.statements]` / `[collection.receipts]`：采集开关与下载目录。
- `[browser]`：登录地址 `https://ebank.cgbchina.com.cn/corporbank/login.jsp`。
- `[cabinet]`：机柜类型；MB 填 `ter_id`/`port`，GF3/USB-HUB 填 `gf3_path`/`product_id`。

## 页面与坑

- 登录流程：打开登录页 → 填用户名（浏览器记忆）→ AiKeyboard 输入登录密码 → 点击登录 → Key盾密码弹窗 → AiKeyboard 输入 PIN → 确认 →（机柜模式）自动点物理 OK 键。
- 键盘输入统一走 AiKeyboard，点击用 JavaScript 定位而不是固定坐标，换分辨率也能用。
- 下载优先 `expect_download`，失败回退到目录轮询。
- 日志在 `logs/`，运行记录在 `records/`，下载文件在 `downloads/`。

## 已知限制

- 没有 `run_task.py`，平台采集任务暂时调不到本程序。
- 登录按钮无响应时先确认浏览器窗口没有被最小化、页面已完全加载。
