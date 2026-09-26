@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Altair

echo ==========================================
echo         Local AI Agent (Desktop)
echo ==========================================

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    echo Install Python 3.10+ and enable "Add Python to PATH".
    pause
    exit /b 1
)

if not exist ".env" (
    echo [SETUP] .env not found - creating from .env.example
    copy /y ".env.example" ".env" >nul
    echo [SETUP] Open .env and set OPENROUTER_API_KEY, then run again.
    pause
    exit /b 1
)

python main.py %*
if errorlevel 1 (
    echo.
    echo [AI Agent] Exited with an error. See logs\agent.log
    pause
)
