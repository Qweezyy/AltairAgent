@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Altair - Setup

echo ==========================================
echo            Altair - first setup
echo ==========================================

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.10+ not found in PATH.
    pause
    exit /b 1
)

echo [1/2] Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
)

echo [2/2] Preparing config...
if not exist ".env" copy /y ".env.example" ".env" >nul

echo.
echo ==========================================
echo  Setup complete. Now run:  run.bat
echo  On first launch, enter your API key
echo  right in the app window - no file editing.
echo ==========================================
pause
