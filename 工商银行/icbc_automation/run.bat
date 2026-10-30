@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"

if "%~1"=="" goto usage
if not exist "%~1" goto missing

if "%BANK_PIN%"=="" set /p BANK_PIN=请输入U盾密码:

python run_task.py --task "%~1"
set CODE=%ERRORLEVEL%

echo.
echo 退出码: %CODE%
if exist "work\result.json" echo 结果文件: %CD%\work\result.json
exit /b %CODE%

:usage
echo 用法: run.bat ^<任务文件.json^>
echo 例:   run.bat task.example.json
exit /b 2

:missing
echo 任务文件不存在: %~1
exit /b 2