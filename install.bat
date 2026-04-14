@echo off
SETLOCAL ENABLEDELAYEDEXPANSION
title Phantom MCP - Installer
color 0A

echo ============================================
echo  Phantom MCP - Auto Installer
echo ============================================
echo.

REM --- Find available Python via py launcher ---
echo Detecting Python versions...
py -0 2>nul
IF ERRORLEVEL 1 (
    echo [ERROR] Python Launcher (py.exe) not found.
    echo Please install Python from https://www.python.org/downloads/
    echo Make sure to check "Add to PATH" and "Install py launcher".
    pause
    exit /b 1
)

REM --- Try preferred versions in order: 3.11, 3.12, 3.10, 3.13, default ---
SET PYVER=
FOR %%V IN (3.11 3.12 3.10 3.13) DO (
    IF "!PYVER!"=="" (
        py -%%V --version >nul 2>&1
        IF NOT ERRORLEVEL 1 (
            SET PYVER=%%V
            echo Found Python %%V - using this version.
        )
    )
)

IF "!PYVER!"=="" (
    echo No preferred version found, using default py...
    SET PYCMD=py
) ELSE (
    SET PYCMD=py -!PYVER!
)

echo Using command: %PYCMD%
echo.

REM --- Upgrade pip first ---
echo Upgrading pip...
%PYCMD% -m pip install --upgrade pip
IF ERRORLEVEL 1 (
    echo [WARNING] pip upgrade failed, continuing anyway...
)

REM --- Install all requirements ---
echo.
echo Installing Phantom MCP requirements...
%PYCMD% -m pip install -r requirements.txt
IF ERRORLEVEL 1 (
    echo.
    echo [ERROR] Some packages failed to install.
    echo Try running this script as Administrator (right-click - Run as administrator)
    pause
    exit /b 1
)

REM --- Verify MCP installed ---
%PYCMD% -m mcp --version >nul 2>&1
IF ERRORLEVEL 1 (
    echo [WARNING] mcp package verification failed. It may still work.
) ELSE (
    echo [OK] MCP framework verified.
)

REM --- Write which python command to use into a config file ---
echo %PYCMD% > .python_cmd.txt

echo.
echo ============================================
echo  Installation complete!
echo  Run launch.bat to start Phantom MCP.
echo ============================================
pause
