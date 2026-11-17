# 宁波银行企业网银采集

Playwright 驱动宁波银行企业网银，采集银行流水和电子回单。目前没有平台 `run_task.py` 入口，按本机脚本使用。

## 运行

```bash
cd c:\wangyingbao\宁波银行\nbcb_automation
python main.py        # 流水 + 回单
python main.py -s     # 仅流水
python main.py -r     # 仅回单
python main.py -c custom.ini
```

目录里的几个 `.bat`：`安装依赖.bat`、`编辑配置.bat`、`启动采集程序.bat`、`打包程序.bat`，双击即可执行对应操作。

## 配置

`config.ini`：

- `[credentials]`：用户名、密码、UKey PIN。
- `[collection.statements]` / `[collection.receipts]`：开关、查询天数、下载目录。
- `[browser]`：登录地址 `https://cb.nbcb.com.cn/microbase/microhome/index.html#/proxyLogin`。
- `[cabinet]`：机柜类型；MB 机柜填 `ter_id` 和 `port`，GF3/USB-HUB（好周岛等）填 `gf3_path` 和 `product_id`。

## 页面与坑

- 登录后先处理可能的证书/弹窗，再进入账户查询。
- UKey 未插入或机柜未挂载时，登录会停在证书选择步骤。
- 日志在 `logs/`，运行记录在 `records/`，下载文件在 `downloads/`。

## 已知限制

- 没有 `run_task.py`，平台采集任务暂时调不到本程序；需要接入时按契约补一个入口。
- 页面选择器随银行改版需要重取。
