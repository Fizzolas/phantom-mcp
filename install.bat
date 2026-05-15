@echo off
SETLOCAL ENABLEDELAYEDEXPANSION
title Phantom MCP - Installer
color 0A

echo ============================================
echo  Phantom MCP - Auto Installer
echo ============================================
echo.

REM --- Safety check: make sure we are in the repo root (server_v2.py must exist) ---
IF NOT EXIST "%~dp0server_v2.py" (
    echo [ERROR] install.bat must be run from the phantom-mcp folder.
    echo         Could not find server_v2.py in: %~dp0
    pause
    exit /b 1
)

REM --- CD to script location so relative paths work ---
cd /d "%~dp0"

REM -----------------------------------------------
REM  Find a usable Python (3.10 - 3.14)
REM  Priority: py launcher versions, then python / python3 on PATH
REM -----------------------------------------------

SET PYCMD=

REM Try py launcher versions in preferred order
IF "!PYCMD!"=="" CALL :TryPyVersion 3.11
IF "!PYCMD!"=="" CALL :TryPyVersion 3.12
IF "!PYCMD!"=="" CALL :TryPyVersion 3.10
IF "!PYCMD!"=="" CALL :TryPyVersion 3.13
IF "!PYCMD!"=="" CALL :TryPyVersion 3.14

REM Try bare py launcher (uses default version)
IF "!PYCMD!"=="" (
    py --version >nul 2>&1
    IF NOT ERRORLEVEL 1 (
        SET PYCMD=py
        echo Found: py ^(default version^)
    )
)

REM Try "python" command (Scoop, conda, venv, most Linux-style installs on Windows)
IF "!PYCMD!"=="" (
    python --version >nul 2>&1
    IF NOT ERRORLEVEL 1 (
        SET PYCMD=python
        echo Found: python
    )
)

REM Try "python3" (MSYS2, WSL proxy, some Conda setups)
IF "!PYCMD!"=="" (
    python3 --version >nul 2>&1
    IF NOT ERRORLEVEL 1 (
        SET PYCMD=python3
        echo Found: python3
    )
)

IF "!PYCMD!"=="" (
    echo.
    echo [ERROR] No Python installation found.
    echo Please install Python 3.10 or newer from https://www.python.org/downloads/
    echo Make sure to check "Add to PATH" during installation.
    pause
    exit /b 1
)

echo.
echo Using Python command: !PYCMD!
!PYCMD! --version
echo.

REM --- Upgrade pip ---
echo Upgrading pip...
!PYCMD! -m pip install --upgrade pip
echo.

REM --- Install all requirements from single requirements.txt ---
echo Installing Phantom MCP requirements...
!PYCMD! -m pip install -r requirements.txt
IF ERRORLEVEL 1 (
    echo.
    echo [ERROR] Some packages failed to install.
    echo   - Try right-clicking install.bat and choosing "Run as administrator"
    echo   - Or activate your virtual environment first, then re-run install.bat
    pause
    exit /b 1
)
echo.

REM --- Install Playwright Chromium browser ---
echo Installing Playwright Chromium browser (required for web tools)...
!PYCMD! -m playwright install chromium
IF ERRORLEVEL 1 (
    echo [WARNING] Playwright Chromium install failed.
    echo   Web browsing and search tools may not work.
    echo   You can retry manually with:
    echo     !PYCMD! -m playwright install chromium
)
echo.

REM --- Install noapi-google-search-mcp via pip (NOT npm) ---
echo Installing noapi-google-search-mcp (Google search, no API key)...
!PYCMD! -m pip install noapi-google-search-mcp
IF ERRORLEVEL 1 (
    echo [WARNING] noapi-google-search-mcp pip install failed.
    echo   Google search tool will not work until this is resolved.
    echo   Retry manually: !PYCMD! -m pip install noapi-google-search-mcp
) ELSE (
    echo noapi-google-search-mcp installed OK.
)
echo.

REM --- Save the resolved python command for launch.bat ---
echo !PYCMD!> .python_cmd.txt

echo ============================================
echo  Installation complete!
echo  Run launch.bat to start Phantom MCP.
echo ============================================
pause
EXIT /B 0

REM -----------------------------------------------
:TryPyVersion
py -%1 --version >nul 2>&1
IF ERRORLEVEL 1 EXIT /B 0
SET PYCMD=py -%1
echo Found Python %1 via py launcher.
EXIT /B 0
