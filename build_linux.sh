#!/usr/bin/env bash
# Збирає один виконуваний файл dist/DeviceTracker (потрібен інтернет для встановлення PyInstaller)
set -e
cd "$(dirname "$0")"
python3 -m pip install --user pyinstaller
python3 -m PyInstaller --onefile --windowed --name DeviceTracker device_tracker.py
echo "Готово: dist/DeviceTracker"
