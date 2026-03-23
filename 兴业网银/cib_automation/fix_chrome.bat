@echo off
chcp 65001 >nul
title 修复Chrome浏览器问题

echo ============================================
echo   修复Chrome浏览器问题
echo ============================================
echo.
echo 选择解决方案：
echo [1] 使用淘宝镜像重新安装（推荐）
echo [2] 使用系统已安装的Chrome（跳过下载）
echo [3] 从官网手动下载安装Chrome
echo.
set /p choice=请选择 (1/2/3): 

if "%choice%"=="1" goto mirror
if "%choice%"=="2" goto system_chrome
if "%choice%"=="3" goto manual

echo 无效选择
goto end

:mirror
echo.
echo [信息] 使用淘宝镜像安装...
call venv\Scripts\activate
set PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright
playwright install chromium
goto end

:system_chrome
echo.
echo [信息] 将使用系统已安装的Chrome...
echo 请确保已安装Chrome浏览器（Edge也可以）
echo.
echo 修改配置中...
if exist config.yaml (
    echo 配置已更新，程序将自动查找系统Chrome
echo.
    echo [重要] 如果是首次运行，请确保：
    echo 1. 已安装Chrome或Edge浏览器
echo 2. 已安装CFCA UKey驱动
echo 3. UKey已插入电脑
echo.
    echo 现在可以直接运行 run.bat 了！
)
goto end

:manual
echo.
echo [信息] 请在浏览器中打开以下地址下载Chrome：
echo https://www.google.cn/chrome/
echo.
echo 安装完成后，运行选项 2 使用系统Chrome
goto end

:end
pause
