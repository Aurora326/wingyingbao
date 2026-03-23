# 广发银行企业网银自动采集程序

基于 Playwright 的广发银行企业网银自动化采集工具，支持银行流水和电子回单的自动下载。

## 功能特性

- 🔐 支持 UKey 证书登录
- 📊 自动采集银行流水（Excel格式）
- 🧾 自动采集电子回单（PDF格式）
- 📅 支持自定义日期范围
- 📁 自动按年月归档文件
- 🧹 自动清理过期数据
- 📝 完整的日志记录
- 🔄 支持失败重试

## 项目结构

```
广发银行/
├── cgb_automation/          # 主程序目录
│   ├── main.py             # 主程序入口
│   ├── browser.py          # 浏览器自动化
│   ├── statement_collector.py  # 流水采集
│   ├── receipt_collector.py    # 回单采集
│   ├── config_loader.py    # 配置加载
│   ├── logger.py           # 日志配置
│   ├── run_recorder.py     # 运行记录
│   ├── config.ini          # 配置文件
│   ├── requirements.txt    # 依赖清单
│   ├── downloads/          # 下载目录
│   │   ├── statements/     # 流水文件
│   │   └── receipts/       # 回单文件
│   ├── logs/               # 日志目录
│   └── records/            # 运行记录
├── build_exe.py            # 打包脚本
└── README.md               # 说明文档
```

## 安装要求

- Python 3.8+
- Windows 操作系统
- Chrome 或 Edge 浏览器
- 广发银行 UKey 数字证书

## 快速开始

### 1. 安装依赖

```bash
cd 广发银行/cgb_automation
pip install -r requirements.txt
playwright install chromium
```

### 2. 配置登录信息

广发银行支持两种登录方式：

#### 方式一：Key盾登录（推荐）
使用 UKey 数字证书登录，**无需处理验证码**，更安全可靠。

```ini
[credentials]
; Key盾登录只需配置 pin
pin = 你的UKey密码

[collection.statements]
enabled = true
start_date = 2026-01-01
end_date = 2026-03-16
```

#### 方式二：免Key登录 + 百度OCR自动识别（推荐）
使用用户名和密码登录，配合百度OCR自动识别验证码，实现全自动登录。

```ini
[credentials]
; 免Key登录需要配置 username 和 password
username = 你的用户名
password = 你的密码

[ocr]
; 启用百度OCR自动识别验证码
enabled = true
api_key = 你的百度API Key
secret_key = 你的百度Secret Key
fallback_manual = true  ; 自动识别失败时允许手动输入

[collection.statements]
enabled = true
start_date = 2026-01-01
end_date = 2026-03-16
```

**获取百度OCR API Key：**
1. 访问 [百度AI开放平台](https://ai.baidu.com/tech/ocr/general)
2. 注册/登录百度账号
3. 创建应用，获取 API Key 和 Secret Key
4. 每月有免费调用额度（5万次），足够日常使用

#### 方式三：免Key登录 + 手动输入验证码
如果不使用百度OCR，也可以手动输入验证码。

```ini
[credentials]
; 免Key登录需要配置 username 和 password
username = 你的用户名
password = 你的密码

[ocr]
; 不启用自动识别
enabled = false

[collection.statements]
enabled = true
start_date = 2026-01-01
end_date = 2026-03-16
```

### 3. 运行程序

```bash
python main.py
```

**登录方式说明：**
- **Key盾登录**：全自动，无需人工干预
- **免Key + 百度OCR**：全自动，验证码自动识别
- **免Key + 手动输入**：程序会弹出验证码输入框

## 使用说明

### 命令行参数

```bash
python main.py                    # 采集流水和回单
python main.py -s                 # 仅采集流水
python main.py -r                 # 仅采集回单
python main.py -c custom.ini      # 使用自定义配置
```

### 配置文件说明

配置文件支持 INI、JSON 和 YAML 格式，默认优先使用 `config.ini`。

#### 主要配置项

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| credentials.pin | UKey PIN 码 | - |
| collection.statements.enabled | 是否启用流水采集 | true |
| collection.receipts.enabled | 是否启用回单采集 | true |
| collection.statements.start_date | 查询开始日期 | - |
| collection.statements.end_date | 查询结束日期 | - |
| browser.headless | 无头模式 | false |

## 打包为可执行文件

```bash
cd 广发银行
python build_exe.py
```

打包后的文件位于 `cgb_automation/dist/CGB_Automation.exe`

## 注意事项

1. **首次使用**：需要先安装浏览器驱动 `playwright install chromium`
2. **UKey 驱动**：确保已安装广发银行 UKey 驱动程序
3. **安全设置**：首次运行可能需要允许浏览器控件安装
4. **窗口焦点**：运行过程中不要最小化浏览器窗口

## 故障排除

### 验证码相关问题
#### 验证码弹窗没有显示
- 确保使用的是免Key登录（配置了 username 和 password）
- 检查是否正确安装了 tkinter（通常Python自带）
- 查看日志确认是否成功截图

#### 验证码输入后登录失败
- 验证码可能已过期，点击验证码图片刷新后重新输入
- 检查用户名和密码是否正确
- 如果多次失败，可以手动完成登录

#### 百度OCR识别失败
- 检查 API Key 和 Secret Key 是否正确
- 确认百度账号有可用额度
- 查看日志中的具体错误信息
- 识别失败时会自动切换到手动输入模式

#### 不想处理验证码怎么办？
- **推荐使用 Key盾登录**：配置 `pin` 即可，无需验证码
- 或**使用百度OCR自动识别**：全自动处理
- 或使用扫码登录：等待程序进入半自动模式后手动扫码

### 无法检测到登录成功
- 检查 UKey 是否已正确插入
- 确认 UKey 密码是否正确
- 查看日志文件了解详细错误信息

### 下载失败
- 检查网络连接
- 确认账户是否有足够权限
- 尝试增大页面超时时间

### 日期设置失败
- 不同版本的网银界面可能略有差异
- 可以尝试手动设置日期后重新运行

## 更新日志

### v1.0.0
- 初始版本发布
- 支持流水和回单采集
- 支持自动登录

## 许可证

本项目仅供学习研究使用，请勿用于非法用途。

## 免责声明

使用本程序产生的任何后果由使用者自行承担，开发者不对因使用本程序造成的任何损失负责。
