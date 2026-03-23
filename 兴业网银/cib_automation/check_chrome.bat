@echo off
chcp 65001 >nul
title 检查Chrome浏览器

echo ============================================
echo   检查系统Chrome浏览器
echo ============================================
echo.

:: 检查常见路径
set found=0

set path1=%ProgramFiles%\Google\Chrome\Application\chrome.exe
set path2=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe
set path3=%LocalAppData%\Google\Chrome\Application\chrome.exe

echo [检查Chrome路径...]
echo.

if exist "%path1%" (
    echo [OK] 找到Chrome:
    echo    %path1%
    set found=1
)

if exist "%path2%" (
    echo [OK] 找到Chrome:
    echo    %path2%
    set found=1
)

if exist "%path3%" (
    echo [OK] 找到Chrome:
    echo    %path3%
    set found=1
)

:: 检查Edge
set edge1=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe
set edge2=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe

if exist "%edge1%" (
    echo [OK] 找到Edge浏览器（Chrome替代）:
    echo    %edge1%
    set found=1
)

if exist "%edge2%" (
    echo [OK] 找到Edge浏览器（Chrome替代）:
    echo    %edge2%
    set found=1
)

if %found%==0 (
    echo [X] 未找到Chrome或Edge浏览器
    echo.
    echo 请从官网下载安装Chrome：
    echo https://www.google.cn/chrome/
    exit /b 1
) else (
    echo.
    echo ============================================
    echo   检测完成！系统已有Chrome/Edge浏览器
    echo   可以直接运行 run.bat 开始采集
    echo ============================================
)

pause
