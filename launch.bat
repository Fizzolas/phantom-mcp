@echo off
title Phantom MCP Server
color 0B

echo ============================================
echo  Phantom MCP - Starting Server
echo ============================================
echo.

REM --- Read saved python command from install ---
SET PYCMD=py
IF EXIST .python_cmd.txt (
    SET /P PYCMD=<.python_cmd.txt
)

echo Python command: %PYCMD%
echo Starting server_v2.py (new registry-based server)...
echo Logs are written to: logs\server_v2.log
echo.
echo [RUNNING] Press Ctrl+C to stop Phantom MCP
echo.

%PYCMD% server_v2.py

IF ERRORLEVEL 1 (
    echo.
    echo [ERROR] Server exited with an error.
    echo Check logs\server_v2.log for details.
    pause
)
