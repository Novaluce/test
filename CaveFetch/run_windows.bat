@echo off
setlocal
title CaveFetch (Windows 10+)
echo ========================================================
echo   CaveFetch - e621 Windows 10+ Desktop Client
echo ========================================================
echo.

where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    echo Please install Python 3.10+ from https://www.python.org/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo Checking dependencies...
python -m pip install --quiet --upgrade pip
python -m pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo [WARNING] Failed to install requirements automatically.
    echo Attempting to start CaveFetch anyway...
)

echo Starting CaveFetch...
python run.py
if %ERRORLEVEL% neq 0 (
    echo.
    echo CaveFetch exited with an error.
    pause
)
