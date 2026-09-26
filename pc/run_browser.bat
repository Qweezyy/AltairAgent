@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Local AI Agent (Web)

echo ==========================================
echo       Local AI Agent (Browser mode)
echo ==========================================

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    pause
    exit /b 1
)

start "" http://127.0.0.1:8000
python main.py --server
pause
