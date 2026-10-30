# 工商银行企业网银自动采集工具

工商银行企业网银自动采集工具。

## 功能特性

- 自动启动浏览器并导航到工行企业网银登录页
- 支持U盾PIN码自动输入（页面安全控件 + Windows弹窗）
- 支持机柜控制器自动插入/拔出U盾、点击OK键
- 支持银行流水自动采集
- 支持电子回单自动采集
- 支持按年月归档下载文件
- 支持运行记录和日志管理

## 重要提示

**工商银行登录后的页面结构（流水查询、回单查询页面）需要在实际环境中调试。**

本程序已完成：
1. ✅ 浏览器启动和工行登录页导航
2. ✅ U盾PIN码处理（支持Playwright和AiKeyboard/pyautogui）
3. ✅ 机柜控制集成
4. ✅ 日志和配置系统
5. ✅ 流水/回单采集器框架已搭建（含 `expect_download` + 文件轮询双保险）
6. ✅ `browser.py` 按 Mixin 模式拆分（`login_manager` / `pin_inputter` / `popup_handler`）
7. ✅ 浏览器生命周期统一继承 `common/base_browser.py`

## 目录结构

```
icbc_automation/
├── main.py                 # 主程序入口
├── browser.py              # 浏览器管理器（继承自 BaseBrowser）
├── login_manager.py        # 登录流程管理（Mixin）
├── pin_inputter.py         # U盾 PIN 自动输入（Mixin）
├── popup_handler.py        # 证书选择/浏览器提示处理（Mixin）
├── statement_collector.py  # 流水采集器
├── receipt_collector.py    # 回单采集器
├── config.ini              # 配置文件
├── requirements.txt        # Python依赖
├── build_exe.py            # 打包脚本
└── README.md               # 本文件

> 公共模块统一维护在 `../../common/`：
> - `base_browser.py` — 浏览器基础类（生命周期管理）
> - `keyboard_helper.py`、`download_helper.py`、`config_loader.py`、`logger.py`、
>   `cabinet_controller.py`、`run_recorder.py`
```

## 安装依赖

```bash
# 创建虚拟环境（可选但推荐）
python -m venv venv
venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器
playwright install chromium
```

## 配置说明

编辑 `config.ini` 文件：

```ini
[credentials]
; U盾 PIN码（工商银行U盾登录必需）
pin = 你的U盾密码

[collection.statements]
enabled = true
start_date = 2026-03-15
end_date = 2026-04-15
file_format = xls

[collection.receipts]
enabled = true

[browser]
; 建议首次运行时设为 false 观察页面
headless = false

[cabinet]
; 如需机柜控制，修改 type = mb 或 gf3
type = mock
```

## 使用方式

```bash
# 采集流水和回单
python main.py

# 仅采集流水
python main.py -s

# 仅采集回单
python main.py -r

# 使用自定义配置
python main.py -c custom_config.ini
```

## 调试说明

### 1. 登录调试

如果自动登录不成功，程序会自动进入"半自动模式"：
- 浏览器保持打开
- 用户手动完成U盾验证和登录
- 程序自动检测登录成功状态并继续执行采集

### 2. 流水/回单采集器调试

由于工商银行登录后的内部页面结构需要实际访问才能确定，你需要：

1. 设置 `headless = false` 运行程序
2. 登录成功后，观察页面上的菜单和元素
3. 修改 `statement_collector.py` 和 `receipt_collector.py` 中的以下部分：
   - `_navigate_to_statement_page()` 中的URL和菜单选择器
   - `_is_statement_page()` 中的页面检测指示器
   - `_set_date_field_by_playwright()` 中的日期输入框选择器
   - `_download_statement()` 中的查询按钮和下载按钮选择器

**下载机制说明**：采集器优先使用 Playwright `expect_download()` 监听下载事件，若银行页面采用 JS 生成方式导致监听失败，会自动回退到原有的文件轮询（`common.download_helper.wait_and_find_new_file`），确保下载稳定可靠。

## 注意事项

- 工商银行企业网银需要使用U盾登录，请确保U盾已正确安装驱动
- 首次运行时建议开启 `headless = false` 以便观察页面
- 如果U盾弹窗无法自动处理，可切换到手动模式或调整PIN输入逻辑
- 机柜控制需要在 `config.ini` 中正确配置 `ter_id`、`port` 等参数
- 程序退出时会强制清理本机所有 `chrome.exe` 进程，避免端口占用（如不希望此行为，请在 `browser.close()` 中传 `kill_all_chrome=False`）

## 更新日志

### v1.1.0 (2026-04-17)
- 提取 `BaseBrowser` 到 `common/base_browser.py`，统一 5 家银行浏览器生命周期管理
- `browser.py` 按 Mixin 模式拆分：`login_manager.py` + `pin_inputter.py` + `popup_handler.py`
- 下载监控升级：优先使用 Playwright `expect_download()`，失败自动回退文件轮询
- 公共模块统一下沉到 `common/`：`keyboard_helper.py`、`download_helper.py`、`config_loader.py`、`logger.py`、`cabinet_controller.py`、`run_recorder.py`

## 技术支持

如有问题，请参考项目文档和日志进行排查。
