@echo off
setlocal enabledelayedexpansion

echo ============================================================
echo  Phantom MCP -- Installer
echo  Sets up the Python environment and all dependencies.
echo ============================================================
echo.

:: -------------------------------------------------------------------
:: 0. Locate Python and verify version (3.8+)
:: -------------------------------------------------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found on PATH.
    echo.
    echo         Install Python 3.11+ from https://python.org
    echo         IMPORTANT: Check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Found %PYVER%

:: Check major.minor >= 3.8
for /f "tokens=2 delims= " %%v in ("%PYVER%") do set PYNUM=%%v
for /f "tokens=1,2 delims=." %%a in ("%PYNUM%") do (
    set PYMAJ=%%a
    set PYMIN=%%b
)
if %PYMAJ% LSS 3 (
    echo [ERROR] Python 3.8 or higher is required. You have %PYVER%.
    echo         Download from https://python.org/downloads/
    pause
    exit /b 1
)
if %PYMAJ% EQU 3 if %PYMIN% LSS 8 (
    echo [ERROR] Python 3.8 or higher is required. You have %PYVER%.
    echo         Download from https://python.org/downloads/
    pause
    exit /b 1
)

:: -------------------------------------------------------------------
:: 1. Create virtual environment
:: -------------------------------------------------------------------
if not exist .venv (
    echo [...] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create .venv
        echo         Try: python -m pip install virtualenv
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
echo [...] Upgrading pip...
python -m pip install --upgrade pip --quiet
echo [OK] pip up to date

:: Save the venv python path so launch.bat can use it reliably
echo .venv\Scripts\python.exe > .python_cmd.txt
echo [OK] Saved python path to .python_cmd.txt

:: -------------------------------------------------------------------
:: 3. Install CORE requirements first (always works; minimal deps)
:: -------------------------------------------------------------------
echo.
echo [...] Installing CORE requirements (requirements-core.txt)...
echo      (mcp, pydantic, httpx, python-dotenv)
pip install -r requirements-core.txt
if errorlevel 1 (
    echo [ERROR] Core pip install failed.
    echo         Check your internet connection and try again.
    echo         If offline, you need: mcp pydantic httpx python-dotenv
    pause
    exit /b 1
)
echo [OK] Core requirements installed

:: -------------------------------------------------------------------
:: 4. Install FULL requirements (optional heavy deps)
:: -------------------------------------------------------------------
echo.
echo [...] Installing full requirements (requirements.txt)...
echo      This includes desktop automation, OCR, vision, and web tools.
echo      Some packages are large. This may take several minutes.
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo.
    echo [WARN] One or more optional packages failed to install.
    echo        The server will still work - tools needing the failed
    echo        package will be automatically hidden from the model.
    echo        Check the output above for details.
    echo.
) else (
    echo [OK] Full requirements installed
)

:: -------------------------------------------------------------------
:: 5. Check for Tesseract-OCR binary
:: -------------------------------------------------------------------
echo.
echo [...] Checking for Tesseract-OCR binary...
where tesseract >nul 2>&1
if errorlevel 1 (
    echo.
    echo [WARN] Tesseract-OCR binary NOT found on PATH.
    echo.
    echo        OCR tools need the Tesseract binary installed separately.
    echo        desktop_watch and other desktop tools do NOT need it.
    echo.
    echo        Install steps:
    echo          1. Download: github.com/UB-Mannheim/tesseract/wiki
    echo          2. Run the installer (default location is fine).
    echo          3. Add Tesseract to PATH:
    echo             Start -^> Search "Environment Variables"
    echo             Edit PATH -^> Add: C:\Program Files\Tesseract-OCR
    echo          4. Re-run install.bat to verify detection.
    echo.
    echo        You can skip this and OCR tools will return a helpful
    echo        error message if called without Tesseract installed.
    echo.
) else (
    echo [OK] Tesseract found on PATH.
)

:: -------------------------------------------------------------------
:: 6. Create required data directories
:: -------------------------------------------------------------------
echo.
echo [...] Creating data directories...
if not exist memory mkdir memory
if not exist logs   mkdir logs
if not exist data   mkdir data
if not exist data\phantom_memory mkdir data\phantom_memory
echo [OK] Directories ready (memory, logs, data, data\phantom_memory)

:: -------------------------------------------------------------------
:: 7. Run server startup validation (checks Python version, paths, etc.)
:: -------------------------------------------------------------------
echo.
echo [...] Running startup validation check...
python -c "import sys; sys.path.insert(0,'.'); exec(open('server_v2.py').read().split('from mcp')[0])" >nul 2>&1
if errorlevel 1 (
    echo [WARN] Validation check returned warnings. Check logs\server_v2.log after first run.
) else (
    echo [OK] Startup validation passed
)

:: -------------------------------------------------------------------
:: 8. Check for reachable AI host (LM Studio or Jan.ai)
:: -------------------------------------------------------------------
echo.
echo [...] Checking for AI host (LM Studio port 1234, Jan.ai port 1337)...

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
    echo        That is OK for now - start LM Studio or Jan.ai before
    echo        using the server. Phantom runs in offline mode until
    echo        a model is loaded.
)

:: -------------------------------------------------------------------
:: 9. Done
:: -------------------------------------------------------------------
echo.
echo ============================================================
echo  Installation complete!
echo.
echo  NEXT STEPS:
echo    1. Start LM Studio (port 1234) OR Jan.ai (port 1337)
echo    2. Load a model with a local server enabled
echo    3. Run launch.bat to start Phantom MCP
echo.
echo  OPTIONAL (for OCR tools):
echo    - Install Tesseract-OCR and add to PATH (see warning above)
echo.
echo  Logs will appear in: logs\server_v2.log
echo ============================================================
echo.
pause
