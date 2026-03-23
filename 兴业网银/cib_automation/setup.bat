@echo off
chcp 65001 >nul
title 兴业网银自动采集工具 - 安装

echo ============================================
echo   兴业银行企业网银自动采集工具 - 安装
echo ============================================
echo.

:: 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到Python，请先安装Python 3.8+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [信息] Python版本:
python --version
echo.

:: 创建虚拟环境
echo [信息] 创建虚拟环境...
python -m venv venv

:: 激活虚拟环境
call venv\Scripts\activate

:: 升级pip
echo [信息] 升级pip...
python -m pip install --upgrade pip

:: 安装依赖
echo [信息] 安装依赖包...
pip install -r requirements.txt

:: 设置国内镜像（加速下载）
echo [信息] 配置国内镜像源...
set PLAYWRIGHT_DOWNLOAD_HOST=https://playwright.azureedge.net
set PLAYWRIGHT_BROWSERS_PATH=0

:: 安装Playwright浏览器
echo [信息] 安装Playwright浏览器（使用国内镜像）...
playwright install chromium

:: 创建配置文件模板
if not exist config.yaml (
    echo [信息] 创建配置文件模板...
    copy config.yaml config.yaml
    echo.
    echo [提示] 请在 config.yaml 中填写您的网银登录信息
)

echo.
echo ============================================
echo   安装完成！
echo ============================================
echo.
echo 请按以下步骤操作：
echo 1. 编辑 config.yaml 文件，填写网银登录信息
echo 2. 确保UKey已插入电脑
echo 3. 双击 run.bat 运行采集程序
echo.
pause
