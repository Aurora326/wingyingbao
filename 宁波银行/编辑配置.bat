@echo off
chcp 65001 >nul
title 编辑配置文件
color 0F

echo ============================================
echo    编辑宁波银行采集程序配置
echo ============================================
echo.

:: 设置工作目录为脚本所在目录
cd /d "%~dp0"
cd nbcb_automation

if exist "config.ini" (
    echo [信息] 正在打开 config.ini...
    start "" notepad.exe "config.ini"
    echo [提示] 请填写：
    echo       - username: 企业编号/操作员编号
    echo       - password: 登录密码
    echo       - pin: UKey PIN码（如需要）
) else (
    echo [错误] 未找到 config.ini 文件
    pause
    exit /b 1
)

echo.
echo [信息] 配置编辑器已打开，保存后关闭即可...
echo.

:: 等待用户关闭记事本
:wait_loop
timeout /t 1 /nobreak >nul
tasklist | findstr "notepad.exe" >nul
if errorlevel 1 (
    echo [信息] 配置文件已关闭
    echo.
    choice /C YN /M "是否立即运行采集程序"
    if errorlevel 1 if not errorlevel 2 call "..\启动采集程序.bat"
) else (
    goto wait_loop
)
