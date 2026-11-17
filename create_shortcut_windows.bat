@echo off
rem Creates a "Device Tracker" shortcut on the Desktop.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\create_shortcut.ps1"
pause
