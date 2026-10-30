@echo off
chcp 65001 >nul
cd /d "%~dp0"
python build_portable.py %*
if errorlevel 1 (
  echo.
  echo 便携目录构建失败。
  pause
  exit /b 1
)
where iscc >nul 2>nul
if errorlevel 1 (
  echo.
  echo 未找到 Inno Setup，便携目录已生成：..\dist\UKeyAgent
  echo 安装 Inno Setup 6 后执行：iscc installer.iss
  pause
  exit /b 0
)
echo.
echo 正在生成安装程序...
iscc installer.iss
if errorlevel 1 (
  echo 安装程序生成失败。
  pause
  exit /b 1
)
echo.
echo 安装程序已生成：..\dist\UKeyAgent-Setup-1.0.exe
pause
