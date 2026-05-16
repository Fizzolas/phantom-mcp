@echo off
title Phantom MCP Server
color 0B

echo ============================================
echo  Phantom MCP - Starting Server
echo ============================================
echo.

:: -------------------------------------------------------------------
:: 1. Locate Python — prefer venv, fall back to system Python
:: -------------------------------------------------------------------
set PYCMD=python

:: Check if .python_cmd.txt was written by install.bat (most reliable)
if exist .python_cmd.txt (
    set /P PYCMD_RAW=<.python_cmd.txt
    :: Strip whitespace/newlines that SET /P may leave
    for /f "tokens=* delims= " %%a in ("%PYCMD_RAW%") do set PYCMD=%%a
)

:: If the saved path doesn't exist, check .venv directly
if not exist "%PYCMD%" (
    if exist .venv\Scripts\python.exe (
        set PYCMD=.venv\Scripts\python.exe
    ) else (
        echo [WARN] Virtual environment not found at .venv
        echo        Falling back to system Python.
        echo        Run install.bat first for best results.
        set PYCMD=python
    )
)

echo Python: %PYCMD%

:: -------------------------------------------------------------------
:: 2. Verify server_v2.py exists
:: -------------------------------------------------------------------
if not exist server_v2.py (
    echo.
    echo [ERROR] server_v2.py not found.
    echo         Make sure you are running this from the phantom-mcp folder.
    echo         Example: cd C:\phantom-mcp  then  launch.bat
    pause
    exit /b 1
)

:: -------------------------------------------------------------------
:: 3. Start the server
:: -------------------------------------------------------------------
echo Starting server_v2.py...
echo Logs: logs\server_v2.log
echo.
echo [RUNNING] Press Ctrl+C to stop Phantom MCP
echo.

"%PYCMD%" server_v2.py

IF ERRORLEVEL 1 (
    echo.
    echo [ERROR] Server exited with an error (code %ERRORLEVEL%).
    echo.
    echo  Common fixes:
    echo    - Run install.bat if you haven't already
    echo    - Check logs\server_v2.log for details
    echo    - Make sure LM Studio or Jan.ai is running
    echo    - Try: "%PYCMD%" -m pip install -r requirements-core.txt
    echo.
    pause
)
