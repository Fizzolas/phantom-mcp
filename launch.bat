@echo off
title Phantom MCP Server
echo ============================================
echo  Phantom MCP Server - Starting...
echo  Press Ctrl+C to stop.
echo ============================================
cd /d "%~dp0"
"C:\Program Files\Python311\python.exe" server.py
pause
