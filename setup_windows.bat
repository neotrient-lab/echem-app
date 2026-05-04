@echo off
REM ============================================================
REM  Neotrient Electrochemical App — One-time setup script (Windows)
REM
REM  WHAT THIS DOES:
REM    1. Checks that Python 3.11 (or newer) is installed
REM    2. Creates a private Python "virtual environment" inside this folder
REM    3. Installs all the libraries the app needs
REM
REM  HOW TO USE:
REM    Just DOUBLE-CLICK this file in File Explorer. A black command
REM    window will pop up and run the setup automatically. When you see
REM    "ALL DONE!" you can close the window.
REM
REM    You only need to run this ONCE per computer.
REM ============================================================

cd /d "%~dp0"

echo.
echo ============================================================
echo    Neotrient Electrochemical App — First-time setup
echo ============================================================
echo.

REM --- Step 1: Check that Python is installed ---------------------------
echo [1/3] Looking for Python 3.11 or newer ...

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo   ERROR: Python is not installed or not in PATH.
    echo.
    echo   Please install Python 3.11 ^(or newer^) before running this script:
    echo     1. Open https://www.python.org/downloads/
    echo     2. Download the latest installer for Windows
    echo     3. IMPORTANT: tick "Add Python to PATH" on the first screen
    echo     4. Run this setup script again
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PY_VERSION=%%i
echo       Found Python %PY_VERSION%

REM --- Step 2: Create the virtual environment ---------------------------
echo.
echo [2/3] Creating private Python environment in .venv\ ...

if exist .venv (
    echo       .venv already exists — removing the old one and starting fresh
    rmdir /s /q .venv
)

python -m venv .venv
if not exist .venv\Scripts\python.exe (
    echo   ERROR: Could not create the virtual environment.
    pause
    exit /b 1
)
echo       Done.

REM --- Step 3: Install dependencies -------------------------------------
echo.
echo [3/3] Installing libraries ^(this can take 1-2 minutes^) ...
echo.

.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
if exist echem_app\requirements.txt (
    .venv\Scripts\pip.exe install -r echem_app\requirements.txt
) else (
    .venv\Scripts\pip.exe install -r requirements.txt
)

echo.
echo ============================================================
echo    ALL DONE! Setup completed successfully.
echo ============================================================
echo.
echo    To start the app:
echo      -^> Close this window
echo      -^> Double-click  start_app.bat  in File Explorer
echo.
pause
