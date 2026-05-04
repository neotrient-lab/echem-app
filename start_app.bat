@echo off
REM ============================================================
REM  Neotrient Electrochemical App — Start the app (Windows)
REM
REM  WHAT THIS DOES:
REM    Starts the web app and opens your default browser at it.
REM
REM  HOW TO USE:
REM    Just DOUBLE-CLICK this file in File Explorer. A black command
REM    window will open and the app will start. Your browser should
REM    pop up automatically showing the app at  http://127.0.0.1:8080
REM
REM    To STOP the app:  click on this command window, then press
REM                      Control + C. Then close the window.
REM
REM    PREREQUISITES:    you must have run setup_windows.bat at least once.
REM ============================================================

cd /d "%~dp0"

echo.
echo ============================================================
echo    Starting Neotrient Electrochemical App ...
echo ============================================================
echo.

if not exist .venv\Scripts\python.exe (
    echo   ERROR: The Python environment hasn't been set up yet.
    echo.
    echo   Please double-click  setup_windows.bat  first to install the
    echo   app, then come back and run start_app.bat.
    echo.
    pause
    exit /b 1
)

set ECHEM_HOST=0.0.0.0
set ECHEM_PORT=8080
set ECHEM_MDNS=1

echo    The app will appear at:  http://127.0.0.1:8080
echo    Your browser should open automatically.
echo.
echo    To STOP the app:  press  Control + C  in this window,
echo                      then close the window.
echo.

.venv\Scripts\python.exe -m echem_app.app
