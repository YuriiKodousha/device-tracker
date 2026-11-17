@echo off
rem Builds dist\DeviceTracker.exe - a single file that runs without Python.
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (set PY=py) else (set PY=python)

echo Installing PyInstaller...
%PY% -m pip install --upgrade pyinstaller
if errorlevel 1 goto fail

echo Building...
%PY% -m PyInstaller --noconfirm --onefile --windowed --name DeviceTracker device_tracker.py
if errorlevel 1 goto fail

echo.
echo  Done: %~dp0dist\DeviceTracker.exe
echo  You can copy this file anywhere (for example to the Desktop).
echo.
pause
exit /b

:fail
echo.
echo  Build failed - see the messages above.
pause
