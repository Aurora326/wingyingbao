@echo off
chcp 65001 >nul
echo ===========================================
echo 中信网银自动化程序打包工具
echo ===========================================
echo.

:: 检查 PyInstaller
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo PyInstaller 未安装，正在安装...
    python -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
)

:: 进入 citic_automation 目录
cd /d %~dp0\citic_automation

echo.
echo 开始打包...
echo.

:: 执行打包
python -m PyInstaller ^
    --name="中信网银自动化" ^
    --onefile ^
    --console ^
    --add-data="config.yaml;." ^
    --add-data="..\..\keyboard;keyboard" ^
    --hidden-import=playwright ^
    --hidden-import=loguru ^
    --hidden-import=yaml ^
    --hidden-import=pyautogui ^
    --hidden-import=pygetwindow ^
    --hidden-import=pyscreeze ^
    --hidden-import=ctypes ^
    --collect-all=playwright ^
    main.py

if errorlevel 1 (
    echo.
    echo 打包失败！
    pause
    exit /b 1
)

echo.
echo ===========================================
echo 打包成功！
echo ===========================================
echo.
echo 可执行文件位置: %~dp0citic_automation\dist\中信网银自动化.exe
echo.

:: 创建发布目录
if not exist "%~dp0发布版" mkdir "%~dp0发布版"
xcopy /Y /I "%~dp0citic_automation\dist\*.exe" "%~dp0发布版\"

:: 复制必要文件
if not exist "%~dp0发布版\config.yaml" (
    copy "%~dp0citic_automation\config.yaml" "%~dp0发布版\config.yaml.example"
    echo 配置文件已复制为 config.yaml.example，请修改为 config.yaml 后使用
)

:: 创建必要的目录
if not exist "%~dp0发布版\logs" mkdir "%~dp0发布版\logs"
if not exist "%~dp0发布版\downloads" mkdir "%~dp0发布版\downloads"

echo.
echo 发布版文件已整理到: %~dp0发布版\
echo.
pause
