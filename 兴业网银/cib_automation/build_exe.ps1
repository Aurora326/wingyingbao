# -*- coding: utf-8 -*-
# 打包脚本：生成可执行文件

# 设置工作目录
$workDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $workDir

Write-Host "========================================"
Write-Host "  兴业银行自动采集工具 - 打包脚本"
Write-Host "========================================"

# 检查 pyinstaller
Write-Host "`n[1/5] 检查 PyInstaller..."
try {
    pyinstaller --version
} catch {
    Write-Host "正在安装 PyInstaller..."
    pip install pyinstaller
}

# 创建必要的目录
Write-Host "`n[2/5] 创建目录结构..."
New-Item -ItemType Directory -Force -Path "dist\downloads\statements" | Out-Null
New-Item -ItemType Directory -Force -Path "dist\downloads\receipts" | Out-Null
New-Item -ItemType Directory -Force -Path "dist\logs" | Out-Null

# 创建 config.yaml 示例文件（给客户用的配置模板）
Write-Host "`n[3/5] 创建配置模板..."
$configContent = @"
# 兴业银行企业网银自动采集配置文件
# 请根据您的实际情况修改以下配置

# 网银登录信息
credentials:
  # 企业网银登录用户名
  username: "您的用户名"
  # UKey PIN码/网盾密码（6-12位）
  pin: "您的网盾密码"

# 采集设置
collection:
  # 银行流水采集设置
  statements:
    # 查询起始日期（天），如采集近7天的流水填7
    days_back: 7
    # 自定义查询日期范围（优先级高于 days_back）
    # 格式：YYYY-MM-DD，留空则使用 days_back 自动计算
    start_date: ""
    end_date: ""
    # 账户列表（为空则自动获取所有账户）
    accounts: []
    # 下载文件保存路径
    download_path: "./downloads/statements"
  
  # 电子回单采集设置
  receipts:
    # 查询起始日期（天）
    days_back: 7
    # 下载文件保存路径
    download_path: "./downloads/receipts"

# 浏览器设置
browser:
  # 网银登录地址
  login_url: "https://corporatebank.cib.com.cn/firm/main/login/login"
  # 是否无头模式（后台运行，建议保持false以便查看操作）
  headless: false
  # Chrome浏览器可执行文件路径（为空则自动查找）
  chrome_path: ""
  # 下载超时（秒）
  download_timeout: 60
  # 页面操作超时（秒）
  page_timeout: 30

# 运行设置
runtime:
  # 日志级别: DEBUG, INFO, WARNING, ERROR
  log_level: "INFO"
  # 最大重试次数
  max_retries: 3
  # 重试间隔（秒）
  retry_delay: 5
"@
Set-Content -Path "dist\config.yaml" -Value $configContent -Encoding UTF8

# 创建使用说明（使用 GB2312 编码确保 Windows 记事本正常显示）
Write-Host "[4/5] 创建使用说明..."
$readmeContent = @"
兴业银行企业网银自动采集工具
================================

【使用前准备】
1. 确保已插入兴业银行的网盾（UKey）
2. 确保已安装 Chrome 或 Edge 浏览器
3. 修改 config.yaml 文件，填写您的网银用户名和网盾密码

【配置文件说明】
打开 config.yaml 文件，修改以下内容：
- credentials.username: 您的网银登录用户名
- credentials.pin: 您的网盾密码（PIN码）
- collection.statements.days_back: 采集最近几天的流水（如7表示最近7天）
- collection.receipts.days_back: 采集最近几天的回单

【运行程序】
双击运行：兴业银行采集工具.exe

【采集结果】
- 银行流水：保存在 downloads\statements 目录
- 电子回单：保存在 downloads\receipts 目录
- 运行日志：保存在 logs 目录

【注意事项】
1. 首次运行可能需要等待浏览器初始化
2. 如果自动登录失败，程序会等待您手动完成登录
3. 运行过程中请勿关闭浏览器窗口
4. 如遇到验证码，需要手动输入

【技术支持】
如有问题，请联系技术支持人员。
"@
$gb2312 = [System.Text.Encoding]::GetEncoding("gb2312")
[System.IO.File]::WriteAllLines("$workDir\dist\使用说明.txt", $readmeContent, $gb2312)

# 执行 PyInstaller 打包
Write-Host "`n[5/5] 开始打包..."
Write-Host "这可能需要几分钟，请耐心等待..."

# crx_extracted 在上级目录，先复制到当前目录
if (Test-Path "..\crx_extracted") {
    Copy-Item -Path "..\crx_extracted" -Destination ".\crx_extracted" -Recurse -Force
    Write-Host "已复制扩展文件到打包目录"
}

pyinstaller --clean --noconfirm `
    --name "兴业银行采集工具" `
    --onefile `
    --console `
    --add-data "config.yaml;." `
    --add-data "crx_extracted;crx_extracted" `
    --hidden-import "playwright" `
    --hidden-import "pyautogui" `
    --hidden-import "yaml" `
    --hidden-import "loguru" `
    main.py

# 检查打包结果
if (Test-Path "dist\兴业银行采集工具.exe") {
    Write-Host "`n========================================"
    Write-Host "  打包成功！"
    Write-Host "========================================"
    Write-Host "输出文件："
    Write-Host "  - dist\兴业银行采集工具.exe"
    Write-Host "  - dist\config.yaml (配置模板)"
    Write-Host "  - dist\使用说明.txt"
    Write-Host "  - dist\downloads\ (下载目录)"
    Write-Host "  - dist\logs\ (日志目录)"
    Write-Host "`n请将整个 dist 文件夹复制给客户使用。"
} else {
    Write-Host "`n打包失败，请检查错误信息。"
}

Write-Host "`n按任意键退出..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
