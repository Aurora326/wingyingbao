@echo off
cd /d "%~dp0"
if not exist ".env" copy /Y ".env.example" ".env" >nul
python -m uvicorn app.main:app --host 127.0.0.1 --port 8800
