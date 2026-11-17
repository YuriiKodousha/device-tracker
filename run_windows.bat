@echo off
rem Device Tracker - start on Windows
cd /d "%~dp0"

where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw "%~dp0device_tracker.py"
    exit /b
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0device_tracker.py"
    exit /b
)

echo.
echo  Python was not found.
echo  Install it from https://www.python.org/downloads/
echo  (during installation tick "Add python.exe to PATH")
echo.
pause
