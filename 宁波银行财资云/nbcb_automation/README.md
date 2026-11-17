# 宁波银行财资云采集

Playwright 驱动宁波银行财资云门户，采集银行流水和电子回单，并把南京银行流水填好的离线导入模板自动导入财资云。目前没有平台 `run_task.py` 入口，按本机脚本使用。

## 运行

```bash
cd c:\wangyingbao\宁波银行财资云\nbcb_automation
python main.py                    # 流水 + 回单，并按配置执行离线导入
python main.py -s                 # 仅流水
python main.py -r                 # 仅回单
python main.py -o                 # 仅执行南京银行离线明细导入
python main.py --no-offline-import  # 跳过离线导入
python main.py -c custom.ini
```

## 配置

`config.ini`：

- `[credentials]`：用户名、密码、UKey PIN。
- `[collection.statements]` / `[collection.receipts]`：采集开关与下载目录。
- `[collection.offline_import]`：南京银行离线明细导入，`source_directory` 留空时自动查南京银行 `downloads/statements`，`filename_pattern` 默认 `离线账户明细导入_已填_*.xls`。
- `[browser]`：登录地址 `https://tgs.nbcb.com.cn/TMS_WEB_HOME/#/simple_login`。
- `[cabinet]`：机柜类型；MB 填 `ter_id`/`port`，GF3/USB-HUB 填 `gf3_path`/`product_id`。

## 页面与坑

- 离线导入流程由 `offline_statement_importer.py` 执行：上传模板 → 发起识别 → 等识别成功 → 提交，各步骤都有超时与轮询配置。
- 识别失败多为模板里的账号、日期与财资云账户不匹配，先确认源流水对应的是本门户的账户。
- 日志在 `logs/`，运行记录在 `records/`。

## 已知限制

- 没有 `run_task.py`，平台采集任务暂时调不到本程序。
- 离线导入依赖南京银行侧先用 `convert_statement_to_template.py` 生成“已填”模板。
