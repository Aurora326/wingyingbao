@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
start "设备上报" cmd /c python device_report.py
start "采集任务" cmd /c python task_worker.py
