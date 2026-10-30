@echo off
chcp 65001 >nul
title 安装宁波银行采集程序依赖
color 0E

echo ============================================
echo    安装宁波银行采集程序依赖
echo ============================================
echo.

:: 设置工作目录为脚本所在目录
cd /d "%~dp0"

:: 检查 Python 是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.8 或更高版本
    echo 下载地址 https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [信息] Python 版本
echo.
python --version
echo.

:: 进入程序目录
cd nbcb_automation

:: 创建虚拟环境（如果不存在）
if not exist "venv" (
    echo [信息] 创建虚拟环境...
    python -m venv venv
)

:: 激活虚拟环境并安装依赖
echo [信息] 安装依赖包...
venv\Scripts\pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

if errorlevel 1 (
    echo.
    echo [错误] 依赖安装失败，请检查网络连接
    pause
    exit /b 1
)

echo.
echo [信息] 安装 Playwright 浏览器...
venv\Scripts\playwright install chromium

echo.
echo ============================================
echo    依赖安装完成！
echo ============================================
echo.
pause
