@echo off
chcp 65001 >nul
title 安装Playwright浏览器（国内镜像）

echo ============================================
echo   安装Playwright浏览器（国内镜像加速）
echo ============================================
echo.

:: 激活虚拟环境
call venv\Scripts\activate

:: 设置国内镜像（腾讯云镜像）
echo [信息] 配置国内镜像源...
set PLAYWRIGHT_DOWNLOAD_HOST=https://playwright.azureedge.net
set PLAYWRIGHT_BROWSERS_PATH=0

echo [信息] 使用镜像: https://playwright.azureedge.net
echo.

:: 安装Chromium浏览器
echo [信息] 正在下载Chromium浏览器（约150MB）...
playwright install chromium

if errorlevel 1 (
    echo.
    echo [错误] 安装失败，尝试使用备用镜像...
    set PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright
    playwright install chromium
)

echo.
echo ============================================
echo   浏览器安装完成！
echo ============================================
pause
