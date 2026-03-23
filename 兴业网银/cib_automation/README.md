# 兴业银行企业网银自动采集工具

自动化采集兴业银行企业网银的银行流水和电子回单，支持UKey常插入模式。

## 功能特点

- ✓ **自动登录**：支持UKey证书自动登录
- ✓ **流水采集**：自动下载指定日期范围的银行流水（Excel/PDF/CSV格式）
- ✓ **回单采集**：自动下载电子回单（支持批量和逐条下载）
- ✓ **多账户支持**：自动识别或指定多个银行账户
- ✓ **灵活配置**：YAML配置文件，易于修改
- ✓ **日志记录**：完整的操作日志，便于排查问题
- ✓ **失败重试**：支持自动重试机制

## 项目结构

```
cib_automation/
├── README.md                 # 使用说明
├── config.yaml              # 配置文件
├── config_loader.py         # 配置加载模块
├── logger.py                # 日志配置模块
├── browser.py               # 浏览器控制器
├── statement_collector.py   # 银行流水采集模块
├── receipt_collector.py     # 电子回单采集模块
├── main.py                  # 主程序入口
├── requirements.txt         # Python依赖
├── setup.bat               # 安装脚本
├── install_browser.bat     # 浏览器安装脚本（国内镜像）
├── run.bat                 # 启动脚本
├── downloads/              # 下载目录
│   ├── statements/        # 流水文件
│   └── receipts/          # 回单文件
└── logs/                  # 日志文件
```

## 安装步骤

### 1. 环境要求

- Windows 7/10/11
- Python 3.8 或更高版本
- Chrome 浏览器 88+
- UKey驱动已安装（CFCA证书管理工具）

### 2. 安装依赖

双击运行安装脚本：

```bash
setup.bat
```

> **注意**：如果Playwright浏览器下载缓慢或失败，脚本会自动使用国内镜像加速。如果仍有问题，请运行 `install_browser.bat` 单独安装浏览器。

或手动安装：

```bash
# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 安装Playwright浏览器
playwright install chromium
```

### 3. 配置登录信息

编辑 `config.yaml` 文件，填写以下信息：

```yaml
credentials:
  username: "你的网银用户名"
  password: "你的网银密码"
  pin: "你的UKey PIN码"  # 如需要

collection:
  statements:
    days_back: 30          # 采集近30天的流水
    file_format: "excel"   # 文件格式: excel/pdf/csv
  
  receipts:
    days_back: 30          # 采集近30天的回单
    organize_by_date: true # 按日期分文件夹存放

browser:
  headless: false          # false=显示浏览器窗口，true=后台运行
```

## 使用方法

### 方式1：双击运行

```bash
# 采集流水和回单
run.bat

# 仅采集流水
run.bat statements

# 仅采集回单
run.bat receipts
```

### 方式2：命令行运行

```bash
# 激活虚拟环境
venv\Scripts\activate

# 采集全部
python main.py

# 仅采集流水
python main.py -s

# 仅采集回单
python main.py -r

# 使用自定义配置
python main.py -c my_config.yaml
```

## 配置文件说明

### `credentials` - 登录凭证

| 参数 | 说明 | 示例 |
|------|------|------|
| username | 企业网银登录用户名 | "admin" |
| password | 登录密码 | "your_password" |
| pin | UKey PIN码（如系统要求） | "123456" |

### `collection.statements` - 流水采集设置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| days_back | 查询起始日期（往前推N天） | 30 |
| accounts | 指定账户列表（空=全部） | [] |
| file_format | 文件格式：excel/pdf/csv | excel |
| download_path | 下载保存路径 | ./downloads/statements |

### `collection.receipts` - 回单采集设置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| days_back | 查询起始日期 | 30 |
| accounts | 指定账户列表 | [] |
| download_path | 下载保存路径 | ./downloads/receipts |
| organize_by_date | 按日期分文件夹存放 | true |

### `browser` - 浏览器设置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| headless | 无头模式（后台运行） | false |
| page_timeout | 页面操作超时（秒） | 30 |
| download_timeout | 下载超时（秒） | 60 |

### `runtime` - 运行设置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| log_level | 日志级别 | INFO |
| max_retries | 最大重试次数 | 3 |
| retry_delay | 重试间隔（秒） | 5 |

## 注意事项

1. **首次运行**：首次运行时需要确认Chrome扩展安装，请允许加载 `CryptoKitExtension.CIB.crx` 扩展

2. **UKey状态**：确保UKey已正确插入且驱动正常识别

3. **页面选择器**：程序中的CSS选择器基于兴业网银常见页面结构，如页面改版可能需要调整

4. **安全建议**：
   - 配置文件包含敏感信息，请妥善保管
   - 建议将 `config.yaml` 加入 `.gitignore`
   - 不要在公共环境泄露密码信息

## 常见问题

### Q: Playwright浏览器下载太慢或失败？
A: 安装脚本已配置国内镜像加速。如果仍然失败：
1. 手动运行 `install_browser.bat`
2. 或手动设置环境变量后安装：
   ```bash
   set PLAYWRIGHT_DOWNLOAD_HOST=https://playwright.azureedge.net
   playwright install chromium
   ```

### Q: 无法识别UKey？
A: 请确保：
- CFCA证书管理工具已正确安装
- UKey已插入且指示灯正常
- 尝试在浏览器中手动登录测试

### Q: 登录失败？
A: 检查：
- 用户名和密码是否正确
- 是否需要输入PIN码
- 网银页面是否有验证码（需要手动处理）

### Q: 页面选择器失效？
A: 兴业网银页面可能会更新，需要：
- 打开 `statement_collector.py` 和 `receipt_collector.py`
- 更新 `SELECTORS` 字典中的选择器

### Q: 如何调试？
A: 修改 `config.yaml`：
```yaml
runtime:
  log_level: "DEBUG"
browser:
  headless: false  # 显示浏览器窗口
```

## 技术说明

本工具基于以下技术栈：
- **Playwright**: 浏览器自动化控制
- **Python 3.8+**: 主程序语言
- **CFCA CryptoKit Extension**: 兴业网银安全控件

## 免责声明

本工具仅供学习和研究使用，请遵守相关法律法规和银行服务条款。使用本工具产生的任何后果由使用者自行承担。

## 更新日志

### v1.0.0 (2024-02-04)
- 初始版本发布
- 支持银行流水自动采集
- 支持电子回单自动采集
- 支持UKey证书登录
