@echo off
chcp 65001 >nul
title 打包宁波银行采集程序
color 0B

echo ============================================
echo    打包宁波银行采集程序
echo ============================================
echo.

:: 设置工作目录为脚本所在目录
cd /d "%~dp0"

:: 检查 Python 是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.8 或更高版本
    pause
    exit /b 1
)

:: 进入程序目录
cd nbcb_automation

:: 检查是否存在虚拟环境
if exist "venv\Scripts\python.exe" (
    echo [信息] 使用虚拟环境运行...
    set PYTHON_CMD=venv\Scripts\python.exe
) else (
    echo [信息] 使用系统 Python 运行...
    set PYTHON_CMD=python
)

:: 检查 pyinstaller 是否安装
%PYTHON_CMD% -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo [信息] 安装 PyInstaller...
    %PYTHON_CMD% -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
)

echo.
echo [信息] 开始打包...
%PYTHON_CMD% build_exe.py

echo.
echo [信息] 打包完成！
echo.

:: 检查是否打包成功
if exist "dist\宁波银行采集程序.exe" (
    echo [成功] 可执行文件已生成：
    echo       %CD%\dist\宁波银行采集程序.exe
    echo.
    choice /C YN /M "是否打开输出目录"
    if errorlevel 1 if not errorlevel 2 start "" explorer.exe "dist"
) else (
    echo [警告] 未找到生成的可执行文件，请检查打包日志
)

pause
