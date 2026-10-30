# 广发银行企业网银自动采集程序

基于 Playwright 的广发银行企业网银自动化采集工具，支持银行流水和电子回单的自动下载。

## 功能特性

- 🔐 支持 Key盾/UKey 证书登录
- 🤖 支持多供应商机柜（MB机柜、GF3/USB-HUB、模拟机等）
- ⌨️ 使用 AiKeyboard 安全输入密码（防键盘记录）
- 📊 自动采集银行流水（Excel 格式：xls/xlsx）
- 🧾 自动采集电子回单（PDF 格式）
- 📅 支持自定义日期范围
- 📁 自动按年月归档文件
- 🧹 自动清理过期数据
- 📝 完整的日志记录
- 🔄 支持失败重试
- 📦 支持打包为独立可执行文件（含 Chromium 浏览器）

## 项目结构

```
广发银行/
├── cgb_automation/              # 主程序目录
│   ├── main.py                 # 主程序入口
│   ├── browser.py              # 浏览器管理器（继承自 BaseBrowser）
│   ├── login_manager.py        # 登录流程管理（Mixin）
│   ├── pin_inputter.py         # PIN 自动输入（Mixin）
│   ├── popup_handler.py        # 弹窗处理（Mixin）
│   ├── statement_collector.py  # 流水采集
│   ├── receipt_collector.py    # 回单采集
│   ├── captcha_recognizer.py   # 验证码识别
│   ├── config.ini              # 配置文件
│   ├── requirements.txt        # 依赖清单
│   ├── build_exe.py            # 打包脚本（含 Chromium）
│   ├── downloads/              # 下载目录
│   │   ├── statements/         # 流水文件
│   │   └── receipts/           # 回单文件
│   ├── logs/                   # 日志目录
│   └── records/                # 运行记录
├── common/                      # 公共共享模块
│   ├── base_browser.py         # 浏览器基础类（生命周期管理）
│   ├── keyboard_helper.py      # AiKeyboard 封装
│   ├── download_helper.py      # 下载监控工具
│   ├── config_loader.py        # 配置加载
│   ├── logger.py               # 日志配置
│   ├── cabinet_controller.py   # 机柜控制器（支持多供应商）
│   └── run_recorder.py         # 运行记录
└── README.md                   # 说明文档
```

## 安装要求

- Python 3.11+（开发环境）
- Windows 10/11 操作系统
- 广发银行 Key盾/UKey 数字证书
- Chrome 浏览器（或使用打包版内置 Chromium）

## 快速开始

### 方式一：使用打包的可执行文件（推荐）

无需安装 Python，直接在目标机器运行：

1. **下载发布包** `广发银行采集程序_vYYYYMMDD.zip`
2. **解压后配置** `config.ini`，填入账号密码
3. **双击运行** `启动程序.bat`

发布包已包含：
- ✅ Chromium 浏览器（无需额外安装 Chrome）
- ✅ AiKeyboard.exe（安全键盘输入工具）
- ✅ mb.exe（MB 机柜控制工具，如需要）

### 方式二：从源码运行

#### 1. 安装依赖

```bash
cd 广发银行/cgb_automation

# 创建虚拟环境
python -m venv venv
.\venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器
playwright install chromium
```

#### 2. 配置登录信息

编辑 `config.ini`：

```ini
[credentials]
username = 你的用户名
password = 你的登录密码
pin = 你的Key盾密码

[collection.statements]
enabled = true
file_format = xls          # 支持 xls 或 xlsx
start_date = 2026-01-01
end_date = 2026-03-23

[collection.receipts]
enabled = false            # 回单下载（如有问题可禁用）

[browser]
headless = false           # 是否无头模式（调试用 false）

[cabinet]
type = mb                  # 机柜类型: mb|gf3|mock|other
manual_mode = false        # 手动模式（用户自己按OK键）

# MB机柜配置
mb_path = C:\waicai\wangyingbao\m2\mb.exe
ter_id = 068330001533
port = 20
user = 15711668178

# GF3机柜配置（好周岛等USB-HUB）
# gf3_path = gf3.exe
# product_id = 1
# port = 1
```

#### 3. 运行程序

```bash
python main.py
```

## 使用说明

### 命令行参数

```bash
python main.py                    # 采集流水和回单
python main.py -s                 # 仅采集流水
python main.py -r                 # 仅采集回单
python main.py -c custom.ini      # 使用自定义配置
```

### 配置文件说明

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| credentials.username | 登录用户名 | - |
| credentials.password | 登录密码 | - |
| credentials.pin | Key盾 PIN 码 | - |
| collection.statements.enabled | 是否启用流水采集 | true |
| collection.statements.file_format | 文件格式 (xls/xlsx) | xls |
| collection.receipts.enabled | 是否启用回单采集 | false |
| browser.headless | 无头模式 | false |
| cabinet.type | 机柜类型 (mb/gf3/mock/other) | mb |
| cabinet.manual_mode | 手动模式（不控制机柜） | false |

## 打包为可执行文件

如需重新打包（包含 Chromium 浏览器）：

```bash
cd 广发银行/cgb_automation
.\venv\Scripts\python build_exe.py
```

打包完成后在 `dist/` 目录生成：
- `广发银行采集程序.exe` - 可执行文件（约 250MB，含 Chromium）
- `广发银行采集程序_vYYYYMMDD.zip` - 完整发布包

## 登录流程说明

### Key盾登录流程

1. 程序自动打开广发银行登录页面
2. 用户名自动填充（浏览器记忆）
3. 使用 **AiKeyboard** 安全输入登录密码
4. JavaScript 点击登录按钮
5. 弹出 Key盾密码输入框
6. 使用 **AiKeyboard** 输入 Key盾 PIN 码
7. 点击确认按钮
8. （如启用机柜控制）自动点击物理 Key盾 OK 键
9. 登录成功，开始采集

### 安全特性

- **AiKeyboard 安全输入**：使用独立进程模拟键盘输入，防止密码被恶意程序截取
- **JavaScript 点击**：避免使用坐标点击，适配不同分辨率（参数化传参，避免字符串注入）
- **多供应商机柜支持**：支持 MB机柜、GF3/USB-HUB机柜（好周岛等）
- **BaseBrowser 基类**：`common/base_browser.py` 统一 5 家银行的浏览器生命周期（启动/关闭/进程清理）
- **Mixin 架构**：`browser.py` 专注银行差异化逻辑，`login_manager` / `pin_inputter` / `popup_handler` 各负其责
- **下载双保险**：优先使用 Playwright `expect_download()` 监听下载，失败自动回退到文件轮询
- **公共模块复用**：`common/keyboard_helper.py`、`common/download_helper.py`、`common/config_loader.py`、`common/logger.py`、`common/cabinet_controller.py`、`common/run_recorder.py` 统一维护

## 故障排除

### 登录按钮点击无响应
- 检查浏览器窗口是否被最小化
- 查看日志确认坐标或 JavaScript 点击是否正常
- 确保页面已完全加载

### Key盾弹窗未处理
- 检查 `pin` 是否配置正确
- 确认 Key盾驱动已安装
- 查看日志确认弹窗检测逻辑

### 机柜控制失败
- 检查 `cabinet.type` 是否配置正确（mb/gf3/mock/other）
- 确认对应机柜的参数配置正确（ter_id/port 或 gf3_path 等）
- 检查网络连接和机柜服务状态
- 查看日志确认机柜类型是否正确识别

### 下载文件格式问题
- 广发银行默认下载 xlsx 格式
- 如需 xls 格式，程序会自动修改文件扩展名
- 注意：实际为 xlsx 格式但扩展名为 xls，Excel 可以正常打开

### 验证码相关问题
- 当前版本使用 Key盾登录**无需验证码**
- 如需免 Key 登录，可配置百度 OCR 自动识别

## 更新日志

### v1.4.0 (2026-04-17)
- 提取 `BaseBrowser` 到 `common/base_browser.py`，统一 5 家银行浏览器生命周期管理
- 重构 `browser.py` 为 Mixin 架构（`login_manager.py` + `pin_inputter.py` + `popup_handler.py`）
- 下载监控升级：主路径使用 Playwright `expect_download()`，失败自动回退文件轮询
- 提取公共模块 `common/keyboard_helper.py`、`common/download_helper.py`、`common/config_loader.py`、`common/logger.py`、`common/cabinet_controller.py`、`common/run_recorder.py`
- 修复 BOM 配置文件解析问题（统一使用 `utf-8-sig`）
- 页面加载等位置使用 Playwright 显式等待替代硬编码 `time.sleep()`
- 退出时强制清理所有 `chrome.exe` 进程

### v1.2.0 (2026-04-13)
- 新增多供应商机柜支持（抽象层+适配器模式）
- 支持 MB机柜、GF3/USB-HUB机柜（好周岛等）、模拟机柜
- 配置文件统一为 `[cabinet]` 段，通过 `type` 字段切换供应商

### v1.1.0 (2026-03-23)
- 新增 AiKeyboard 安全密码输入
- 新增 MB 机柜机器人支持（物理 Key盾自动操作）
- 支持打包为独立可执行文件（含 Chromium）
- 优化登录流程，使用 JavaScript 点击避免坐标问题
- 新增 xls/xlsx 格式支持
- 清理项目结构，移除多余文件

### v1.0.0
- 初始版本发布
- 支持流水和回单采集
- 支持自动登录

## 许可证

本项目仅供学习研究使用，请勿用于非法用途。

## 免责声明

使用本程序产生的任何后果由使用者自行承担，开发者不对因使用本程序造成的任何损失负责。
