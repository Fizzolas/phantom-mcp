@echo off
setlocal enabledelayedexpansion

echo ============================================================
echo  Phantom MCP — Installer
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
    echo        Phantom's OCR tools (desktop_ocr, desktop_find_text,
    echo        desktop_wait_for_text) require the Tesseract binary.
    echo        desktop_watch does NOT require Tesseract.
    echo.
    echo        Install steps:
    echo          1. Download the Windows installer from:
    echo             https://github.com/UB-Mannheim/tesseract/wiki
    echo          2. Run the installer (default location is fine).
    echo          3. Add Tesseract to PATH:
    echo             System Properties -^> Advanced -^> Environment Variables
    echo             Add "C:\Program Files\Tesseract-OCR" to the Path variable.
    echo          4. Re-run install.bat to verify it is detected.
    echo.
    echo        You can continue without Tesseract; OCR tools will return
    echo        ok=false with a clear error and the install hint above.
    echo.
) else (
    for /f "tokens=*" %%t in ('tesseract --version 2^>^&1 ^| findstr /i "tesseract"') do (
        echo [OK] Found %%t
        goto :tesseract_found
    )
    :tesseract_found
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
:: 8. Verify LM Studio is reachable (optional, soft check)
:: -------------------------------------------------------------------
echo.
echo [..] Checking LM Studio API at http://localhost:1234 ...
curl -s --max-time 3 http://localhost:1234/v1/models >nul 2>&1
if errorlevel 1 (
    echo [WARN] LM Studio not reachable on port 1234.
    echo        Start LM Studio, load a model, and enable the local server
    echo        before running launch.bat.
) else (
    echo [OK] LM Studio API is reachable
)

:: -------------------------------------------------------------------
:: 9. Done
:: -------------------------------------------------------------------
echo.
echo ============================================================
echo  Installation complete!
echo.
echo  REQUIRED before first run:
echo    - LM Studio running with a model loaded (local server on port 1234)
echo    - Tesseract-OCR installed + on PATH (for OCR tools)
echo.
echo  Run launch.bat to start the Phantom MCP server.
echo ============================================================
echo.
pause
