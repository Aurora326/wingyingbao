@echo off
chcp 65001 >nul
title 宁波银行企业网银采集程序
color 0A

echo ============================================
echo    宁波银行企业网银自动采集程序
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

:: 检查是否存在虚拟环境
if exist "nbcb_automation\venv\Scripts\python.exe" (
    echo [信息] 使用虚拟环境运行
    set PYTHON_CMD=nbcb_automation\venv\Scripts\python.exe
) else (
    echo [信息] 使用系统 Python 运行
    set PYTHON_CMD=python
)

:: 进入程序目录
cd nbcb_automation

:: 显示菜单
:menu
cls
echo ============================================
echo    宁波银行企业网银自动采集程序
echo ============================================
echo.
echo  [1] 采集流水和回单（默认）
echo  [2] 仅采集银行流水
echo  [3] 仅采集电子回单
echo  [4] 打开配置文件夹
echo  [5] 查看日志文件
echo  [0] 退出
echo.
echo ============================================
set /p choice=请选择操作 [0-5]: 

if "%choice%"=="1" goto run_all
if "%choice%"=="2" goto run_statements
if "%choice%"=="3" goto run_receipts
if "%choice%"=="4" goto open_config
if "%choice%"=="5" goto open_logs
if "%choice%"=="0" goto exit

echo [错误] 无效的选择，请重新输入
pause
goto menu

:run_all
echo.
echo [信息] 开始采集流水和回单
%PYTHON_CMD% main.py
pause
goto menu

:run_statements
echo.
echo [信息] 开始仅采集银行流水
%PYTHON_CMD% main.py -s
pause
goto menu

:run_receipts
echo.
echo [信息] 开始仅采集电子回单
%PYTHON_CMD% main.py -r
pause
goto menu

:open_config
echo.
echo [信息] 打开配置文件夹
if exist "config.ini" (
    start "" notepad.exe "config.ini"
) else (
    echo [警告] 未找到 config.ini 文件
    pause
)
goto menu

:open_logs
echo.
echo [信息] 打开日志文件夹
if exist "logs" (
    start "" explorer.exe "logs"
) else (
    echo [警告] 未找到日志文件夹
    pause
)
goto menu

:exit
echo.
echo [信息] 感谢使用，再见！
timeout /t 2 /nobreak >nul
exit /b 0
