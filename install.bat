@echo off
SETLOCAL ENABLEDELAYEDEXPANSION
title Phantom MCP - Installer
color 0A

echo ============================================
echo  Phantom MCP - Auto Installer
echo ============================================
echo.

REM --- Check py launcher exists ---
py --version >nul 2>&1
IF ERRORLEVEL 1 (
    echo [ERROR] Python Launcher ^(py.exe^) not found.
    echo Please install Python from https://www.python.org/downloads/
    echo Make sure to check "Add to PATH" and "Install py launcher".
    pause
    exit /b 1
)

echo Detecting Python versions...
py -0
echo.

REM --- Try 3.11 first (best for AI/ML) ---
SET PYCMD=
CALL :TryVersion 3.11
IF NOT "!PYCMD!"=="" GOTO :GotPython

REM --- Try 3.12 ---
CALL :TryVersion 3.12
IF NOT "!PYCMD!"=="" GOTO :GotPython

REM --- Try 3.10 ---
CALL :TryVersion 3.10
IF NOT "!PYCMD!"=="" GOTO :GotPython

REM --- Try 3.13 ---
CALL :TryVersion 3.13
IF NOT "!PYCMD!"=="" GOTO :GotPython

REM --- Try 3.14 ---
CALL :TryVersion 3.14
IF NOT "!PYCMD!"=="" GOTO :GotPython

REM --- Fall back to default py ---
echo No specific version matched, using default py...
SET PYCMD=py

:GotPython
echo Using: %PYCMD%
echo.

REM --- Upgrade pip ---
echo Upgrading pip...
%PYCMD% -m pip install --upgrade pip
echo.

REM --- Install requirements ---
echo Installing Phantom MCP requirements...
%PYCMD% -m pip install -r requirements.txt
IF ERRORLEVEL 1 (
    echo.
    echo [ERROR] Some packages failed to install.
    echo Try right-clicking install.bat and choosing "Run as administrator"
    pause
    exit /b 1
)

REM --- Save the python command for launch.bat ---
echo %PYCMD%> .python_cmd.txt

REM --- Check for Node.js / npm (required for google-search MCP plugin) ---
echo.
echo Checking for Node.js and npm (required for google-search plugin)...
where npm >nul 2>&1
IF ERRORLEVEL 1 (
    echo [WARNING] npm not found. The google-search MCP plugin will not work.
    echo Download Node.js from https://nodejs.org/ and re-run install.bat.
) ELSE (
    echo npm found. Checking for noapi-google-search-mcp...
    where noapi-google-search-mcp >nul 2>&1
    IF ERRORLEVEL 1 (
        echo noapi-google-search-mcp not found. Installing globally via npm...
        npm install -g noapi-google-search-mcp
        IF ERRORLEVEL 1 (
            echo [ERROR] Failed to install noapi-google-search-mcp.
            echo Try running: npm install -g noapi-google-search-mcp
            echo as Administrator in a separate terminal, then re-run install.bat.
        ) ELSE (
            echo noapi-google-search-mcp installed successfully.
        )
    ) ELSE (
        echo noapi-google-search-mcp is already installed and on PATH. OK.
    )
)

echo.
echo ============================================
echo  Installation complete!
echo  Run launch.bat to start Phantom MCP.
echo ============================================
pause
EXIT /B 0

REM -----------------------------------------------
:TryVersion
py -%1 --version >nul 2>&1
IF ERRORLEVEL 1 EXIT /B 0
SET PYCMD=py -%1
echo Found Python %1 - will use this version.
EXIT /B 0
