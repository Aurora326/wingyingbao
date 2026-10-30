# 工商银行企业网银自动采集工具

基于 Playwright 的工商银行企业网银自动化采集程序，架构与 `中信网银` 保持一致。

登录流程已按工行新一代企业网银（`https://corporbank-simp.icbc.com.cn/frame/web/index.html`）适配：
点击“U盾登录” → 自动确认证书选择 → 虚拟键盘输入 U盾密码 → 检测登录成功。
流水/回单页面的具体字段仍可按实际页面继续详调。

## 目录结构

```
工商银行/
├── icbc_automation/
│   ├── browser.py              # 浏览器自动化 / 登录 / 安全控件 / PIN 输入
│   ├── config_loader.py        # 配置加载（INI / JSON / YAML）
│   ├── config.ini              # 配置文件（含敏感信息，勿提交）
│   ├── config.json.example     # JSON 配置示例
│   ├── logger.py               # 日志模块
│   ├── main.py                 # 主程序入口
│   ├── receipt_collector.py    # 电子回单采集模块
│   ├── requirements.txt        # 依赖列表
│   ├── run_recorder.py         # 运行结果记录
│   ├── statement_collector.py  # 银行流水采集模块
│   └── .gitignore
├── build_exe.py                # PyInstaller 打包脚本
├── README.md
└── .gitignore
```

## 安装

```bash
cd 工商银行/icbc_automation
pip install -r requirements.txt
playwright install chromium
```

## 配置

程序优先读取 `config.ini`，其次 `config.json` / `config.yaml`。

```ini
[credentials]
username = 您的用户名
password = 您的密码
pin = 您的UKey PIN码

[collection.statements]
enabled = true
days_back = 30
start_date =
end_date =
download_path = ./downloads/statements

[collection.receipts]
enabled = true
days_back = 30
download_path = ./downloads/receipts

[browser]
login_url = https://corporbank-simp.icbc.com.cn/frame/web/index.html
headless = false
```

## 使用方法

```bash
python main.py          # 采集流水和回单
python main.py -s       # 仅采集流水
python main.py -r       # 仅采集回单
python main.py -c custom_config.ini
```

## 当前已适配

- 浏览器启动（持久化用户目录、固定窗口、下载路径）
- 登录页：`https://corporbank-simp.icbc.com.cn/frame/web/index.html`
- 自动切换到“企业网银”页签并点击“U盾登录”
- 原生证书选择窗口：自动回车确认默认证书
- U盾密码窗口：`AiKeyboard.exe` 虚拟键盘输入 PIN，回车提交
- 登录成功检测：退出 / 欢迎页面 / 常用功能 / 跳转域名
- 登录后关闭常见广告弹层
- 流水采集：点击“账户管理-明细查询”菜单、iframe 自动定位、日期设置、查询、空数据标记、下载监听、按年月归档、旧数据清理
- 回单采集：点击“账户管理-电子回单”菜单、iframe 自动定位、查询、全选、下载、空数据标记、归档
- 运行记录（`records/latest.json`）
- 日志按天轮转与过期清理
- PyInstaller 打包脚本

## 后续详调清单（TODO）

1. **browser.py**
   - 若证书弹窗未自动确认，检查 `_handle_certificate` 的窗口标题匹配
   - 若 U盾密码未输入成功，检查 `_handle_ukey_password` 的焦点/输入方式
   - 登录后广告弹层的关闭选择器
2. **statement_collector.py**
   - 明细查询页的日期控件、账户下拉框、查询/下载按钮选择器
   - 下载格式（XLS/CSV/PDF）与工行实际导出一致性
   - 查询结果为空时的提示文案
3. **receipt_collector.py**
   - 电子回单菜单路径与查询页元素
   - 回单 PDF 下载/打印的弹窗或新标签页处理
4. **其它**
   - 是否需要按账户逐个下载
   - 文件命名与归档策略确认

## 文件命名

- 流水：`工商银行_银行流水_yyyymmdd.xlsx`
- 回单：`工商银行_电子回单_yyyymmdd.pdf`
- 无数据标记：`工商银行_银行流水_无数据_yyyymmdd_HHMMSS.txt`

## 日志

`logs/icbc_automation_YYYYMMDD.log`，默认保留 30 天。

## 注意事项

1. 运行前插入工行 U盾 并确认驱动已安装
2. 必须使用有界面的 Chrome/Edge（`headless = false`），U盾证书/密码框是 Windows 原生弹窗
3. 首次运行会弹出证书选择，程序默认回车确认第一张证书；如需选择其他证书请手动操作
4. U盾密码通过 `AiKeyboard.exe` 输入，需保证 `keyboard` 目录与程序同级
5. 推荐屏幕分辨率 1920x1080 以上
6. 首次使用建议先用 `python main.py -s` 单独验证流水采集

## 技术栈

- Playwright：浏览器自动化
- PyYAML：配置文件解析
- Loguru：日志记录
- PyAutoGUI：输入法切换辅助
- AiKeyboard.exe：UKey 密码虚拟键盘输入
