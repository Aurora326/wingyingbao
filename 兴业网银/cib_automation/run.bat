@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title 兴业网银自动采集工具

echo ============================================
echo   兴业银行企业网银自动采集工具
echo ============================================
echo.

:: 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到Python，请安装 Python 3.8+
    pause
    exit /b 1
)

:: 创建虚拟环境
if not exist venv (
    echo [信息] 创建虚拟环境...
    python -m venv venv
)

:: 激活虚拟环境
call venv\Scripts\activate

:: 安装依赖
echo [信息] 检查依赖...
pip install -q -r requirements.txt

:: 检查配置文件
if not exist config.yaml (
    echo [错误] 缺少配置文件 config.yaml
    echo 请复制 config.yaml.example 为 config.yaml 并进行配置
    pause
    exit /b 1
)

:: 运行程序
echo [信息] 启动程序...
echo.

if "%1"=="statements" (
    python main.py -s
) else if "%1"=="receipts" (
    python main.py -r
) else (
    python main.py
)

echo.
echo [信息] 程序已结束
pause
