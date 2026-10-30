@echo off
chcp 65001 >nul
title 宁波银行采集程序 - 快速启动
color 0A

:: 设置工作目录为脚本所在目录
cd /d "%~dp0"

:: 检查 Python 是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.8 或更高版本
    pause
    exit /b 1
)

:: 检查是否存在虚拟环境
if exist "nbcb_automation\venv\Scripts\python.exe" (
    set PYTHON_CMD=nbcb_automation\venv\Scripts\python.exe
) else (
    set PYTHON_CMD=python
)

cd nbcb_automation

echo ============================================
echo    宁波银行企业网银自动采集程序
echo ============================================
echo.
echo 正在启动，默认采集【流水和回单】...
echo.

%PYTHON_CMD% main.py

pause
