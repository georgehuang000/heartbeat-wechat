@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0心动微信.exe" (
    start "" "%~dp0心动微信.exe"
    exit /b
)
start "" /b ".venv\Scripts\pythonw.exe" "main.py"
