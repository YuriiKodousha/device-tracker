@echo off
rem Starts the program WITH a console window - use it if the program does not open,
rem the console will show the error message.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py "%~dp0device_tracker.py"
) else (
    python "%~dp0device_tracker.py"
)
echo.
echo  Program closed. If you see an error above, send a screenshot of it.
pause
