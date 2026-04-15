@echo off
title NEXUS v5 - Shipping Intelligence
echo.
echo  ==========================================
echo   NEXUS Shipping Intelligence v5
echo   Starting server at http://localhost:5000
echo  ==========================================
echo.
cd /d "%~dp0"
python -m backend.app
pause
title NEXUS Shipping Intelligence v2.0
echo.
echo  ======================================================
echo   NEXUS Shipping Intelligence v2.0
echo  ======================================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found. Please install Python 3.9+
    echo  Download: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Install dependencies
echo  Installing/checking dependencies...
pip install -r requirements.txt -q

:: Start server and open browser
echo  Starting Nexus backend...
start "" http://localhost:5000
python backend/app.py

pause
