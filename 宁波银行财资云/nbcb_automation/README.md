# 宁波银行企业网银自动采集程序

宁波银行企业网银自动化采集工具。

## 功能特性

- ✅ 自动登录宁波银行企业网银
- ✅ 银行流水自动采集（Excel/PDF/CSV格式）
- ✅ 电子回单自动采集
- ✅ 南京银行离线明细模板自动导入宁波银行财资云
- ✅ 验证码自动识别（可选，需配置百度OCR）
- ✅ 多供应商机柜支持（MB机柜、GF3/USB-HUB机柜等）
- ✅ 按年月归档数据，自动清理旧数据
- ✅ 详细的日志记录
- ✅ 运行记录保存

## 登录地址

https://cb.nbcb.com.cn/microbase/microhome/index.html#/proxyLogin

## 安装

### 1. 安装依赖

```bash
pip install -r requirements.txt

# 安装Playwright浏览器
playwright install chromium
```

### 2. 配置文件

复制 `config.ini` 并根据实际情况填写：

```ini
[credentials]
username = 你的企业编号
password = 你的登录密码
pin = UKey PIN码（如需要）
```

## 使用方法

### 命令行运行

```bash
# 采集流水和回单（默认），并根据配置执行离线导入
python main.py

# 仅采集流水
python main.py -s

# 仅采集回单
python main.py -r

# 仅执行南京银行离线明细导入
python main.py -o

# 跳过离线导入
python main.py --no-offline-import

# 使用自定义配置文件
python main.py -c my_config.ini
```

### 打包为可执行文件

```bash
python build_exe.py
```

打包完成后，在 `dist` 目录下会生成 `宁波银行采集程序.exe`。

## 配置文件说明

```ini
[credentials]
username = 企业编号/操作员编号
password = 登录密码
pin = UKey PIN码

[collection.statements]
enabled = true              # 启用流水采集
days_back = 30              # 查询最近30天
start_date =                # 自定义开始日期 YYYY-MM-DD
end_date =                  # 自定义结束日期 YYYY-MM-DD
file_format = excel         # 文件格式: excel, pdf, csv
archive_by_month = true     # 按年月归档

[collection.receipts]
enabled = true              # 启用回单采集

[collection.offline_import]
enabled = false             # 是否启用南京银行离线明细导入
source_directory =          # 模板搜索目录，空则自动查找南京银行 downloads/statements
filename_pattern = 离线账户明细导入_已填_*.xls
click_recognize = true      # 上传后是否自动点击发起识别
wait_recognize_success = true   # 发起识别后是否等待识别成功
click_submit = true         # 识别成功后是否自动点击提交
recognize_timeout = 30      # 等待识别成功超时（秒）
recognize_refresh_interval = 3  # 识别状态刷新间隔（秒）
upload_timeout = 30         # 等待上传超时（秒）

[cabinet]
type = mb                   # 机柜类型: mb|gf3|mock|other
manual_mode = false         # 手动模式（用户自己按OK键）

# MB机柜配置
mb_path = mb
ter_id = 机柜编号
port = 1
user = 用户名

# GF3机柜配置（好周岛等USB-HUB）
# gf3_path = gf3.exe
# product_id = 1
# port = 1
```

## 目录结构

```
宁波银行/
├── nbcb_automation/
│   ├── main.py                 # 主程序入口
│   ├── browser.py              # 浏览器管理器（继承自 BaseBrowser）
│   ├── login_manager.py        # 登录流程管理（Mixin）
│   ├── pin_inputter.py         # PIN 自动输入（Mixin）
│   ├── popup_handler.py        # 弹窗处理（Mixin）
│   ├── statement_collector.py  # 流水采集模块
│   ├── receipt_collector.py    # 回单采集模块
│   ├── offline_statement_importer.py  # 南京银行离线明细导入模块
│   ├── captcha_recognizer.py   # 验证码识别
│   ├── config.ini              # 配置文件模板
│   ├── requirements.txt        # 依赖列表
│   ├── build_exe.py            # 打包脚本
│   └── README.md               # 使用说明
├── common/                      # 公共共享模块
│   ├── base_browser.py         # 浏览器基础类（生命周期管理）
│   ├── keyboard_helper.py      # AiKeyboard 封装
│   ├── download_helper.py      # 下载监控工具
│   ├── config_loader.py        # 配置加载
│   ├── logger.py               # 日志配置
│   ├── cabinet_controller.py   # 机柜控制器（支持多供应商）
│   └── run_recorder.py         # 运行记录
```

## 注意事项

1. **首次运行**：首次运行时需要手动配置登录信息
2. **UKey**：如果使用UKey登录，请确保UKey已插入或配置MB机柜自动插拔
3. **验证码**：如需自动识别验证码，需要配置百度OCR API
4. **浏览器**：程序会自动查找系统安装的Chrome或Edge浏览器
5. **架构升级**：`browser.py` 已按 Mixin 模式拆分为 `login_manager.py`、`pin_inputter.py`、`popup_handler.py`，并统一继承 `common/base_browser.py` 管理生命周期
6. **下载监控**：采集器优先使用 Playwright `expect_download()` 监听下载，失败时自动回退文件轮询
7. **进程清理**：程序退出时会强制关闭本机所有 `chrome.exe` 进程，避免残留占用（可配置 `kill_all_chrome=False` 关闭）

## 适配说明

当前版本已完成针对宁波银行企业网银的独立适配，主要包括：

1. **登录URL**：更新为宁波银行企业网银登录地址
2. **页面元素**：适配宁波银行的页面结构和元素选择器
3. **导航菜单**：适配宁波银行的菜单结构和导航方式
4. **日期字段**：适配宁波银行的日期输入字段名称
5. **公共模块**：统一接入 `common/keyboard_helper.py`、`common/download_helper.py`、`common/config_loader.py`、`common/logger.py`、`common/cabinet_controller.py`、`common/run_recorder.py`
6. **显式等待**：页面加载、元素交互优先使用 Playwright 原生等待，减少硬编码 `time.sleep()`

## 日志

日志文件保存在 `logs/` 目录下，按日期命名：
- `nbcb_automation_YYYYMMDD.log`

## 数据文件

下载的文件保存在以下目录：
- 流水：`downloads/statements/YYYY/MM/`
- 回单：`downloads/receipts/YYYY/MM/`

## 运行记录

每次运行的详细记录保存在 `records/` 目录下，格式为JSON。

## 技术支持

如有问题，请检查日志文件或联系技术支持。
