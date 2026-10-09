@echo off
title CaveFetch
echo ========================================================
echo Starting CaveFetch - e621 Browser & Downloader
echo ========================================================
echo Checking Python environment...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    echo Please install Python from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

echo Installing / verifying dependencies (requests, pillow, av, sounddevice)...
python -m pip install -q -r requirements.txt
if %errorlevel% neq 0 (
    echo Trying user-level install...
    python -m pip install --user -q -r requirements.txt
)

echo Launching CaveFetch...
python run.py
if %errorlevel% neq 0 (
    echo.
    echo CaveFetch closed with an error.
    pause
)
