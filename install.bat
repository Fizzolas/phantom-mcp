@echo off
setlocal enabledelayedexpansion

echo ============================================================
echo  Phantom MCP -- Installer
echo  Sets up the Python environment and all dependencies.
echo ============================================================
echo.

:: -------------------------------------------------------------------
:: 0. Locate Python
:: -------------------------------------------------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found on PATH.
    echo         Install Python 3.11+ from https://python.org and re-run.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Found %PYVER%

:: -------------------------------------------------------------------
:: 1. Create virtual environment
:: -------------------------------------------------------------------
if not exist .venv (
    echo [..] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create .venv
        pause
        exit /b 1
    )
    echo [OK] .venv created
) else (
    echo [OK] .venv already exists, skipping creation
)

:: -------------------------------------------------------------------
:: 2. Activate + upgrade pip
:: -------------------------------------------------------------------
call .venv\Scripts\activate.bat
echo [..] Upgrading pip...
python -m pip install --upgrade pip --quiet
echo [OK] pip up to date

:: -------------------------------------------------------------------
:: 3. Install Python dependencies
:: -------------------------------------------------------------------
echo [..] Installing Python requirements (requirements.txt)...
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] pip install failed. Check requirements.txt and your internet connection.
    pause
    exit /b 1
)
echo [OK] Python requirements installed

:: -------------------------------------------------------------------
:: 4. Install pytesseract (OCR Python binding)
:: -------------------------------------------------------------------
echo [..] Installing pytesseract...
pip install pytesseract --quiet
if errorlevel 1 (
    echo [WARN] pytesseract install failed. OCR tools will not work.
    echo        Run manually: pip install pytesseract
) else (
    echo [OK] pytesseract installed
)

:: -------------------------------------------------------------------
:: 5. Install numpy (required for desktop_watch pixel-diff monitoring)
:: -------------------------------------------------------------------
echo [..] Installing numpy...
pip install numpy --quiet
if errorlevel 1 (
    echo [WARN] numpy install failed. desktop_watch will not work.
    echo        Run manually: pip install numpy
) else (
    echo [OK] numpy installed
)

:: -------------------------------------------------------------------
:: 6. Check for Tesseract-OCR binary
:: -------------------------------------------------------------------
echo.
echo [..] Checking for Tesseract-OCR binary...
where tesseract >nul 2>&1
if errorlevel 1 (
    echo.
    echo [WARN] Tesseract-OCR binary NOT found on PATH.
    echo.
    echo        Phantom's OCR tools require the Tesseract binary.
    echo        desktop_watch does NOT require Tesseract.
    echo.
    echo        Install steps:
    echo          1. Download: github.com/UB-Mannheim/tesseract/wiki
    echo          2. Run the installer ^(default location is fine^).
    echo          3. Add Tesseract to PATH:
    echo             System Properties -^> Advanced -^> Environment Variables
    echo             Add "C:\Program Files\Tesseract-OCR" to the Path variable.
    echo          4. Re-run install.bat to verify detection.
    echo.
    echo        OCR tools will return ok=false with an install hint if missing.
    echo.
) else (
    echo [OK] Tesseract found on PATH.
)

:: -------------------------------------------------------------------
:: 7. Create required data directories
:: -------------------------------------------------------------------
echo.
echo [..] Creating data directories...
if not exist memory mkdir memory
if not exist logs   mkdir logs
if not exist data   mkdir data
echo [OK] Directories ready (memory, logs, data)

:: -------------------------------------------------------------------
:: 8. Check for reachable AI host (LM Studio or Jan.ai)
:: -------------------------------------------------------------------
echo.
echo [..] Checking for AI host (LM Studio port 1234, Jan.ai port 1337)...

set HOST_FOUND=0

curl -s --max-time 3 http://localhost:1234/v1/models >nul 2>&1
if not errorlevel 1 (
    echo [OK] LM Studio API is reachable at http://localhost:1234
    set HOST_FOUND=1
)

curl -s --max-time 3 http://localhost:1337/v1/models >nul 2>&1
if not errorlevel 1 (
    echo [OK] Jan.ai API is reachable at http://localhost:1337
    set HOST_FOUND=1
)

if %HOST_FOUND%==0 (
    echo [WARN] No AI host found on port 1234 or 1337.
    echo        Phantom starts in offline mode - memory/file/shell tools still work.
    echo        Start LM Studio or Jan.ai with a model loaded before running launch.bat.
)

:: -------------------------------------------------------------------
:: 9. Done
:: -------------------------------------------------------------------
echo.
echo ============================================================
echo  Installation complete!
echo.
echo  REQUIRED before first run:
echo    - LM Studio ^(port 1234^) OR Jan.ai ^(port 1337^) running with
echo      a model loaded and local server enabled
echo    - Tesseract-OCR installed + on PATH ^(for OCR tools only^)
echo.
echo  Run launch.bat to start the Phantom MCP server.
echo ============================================================
echo.
pause
