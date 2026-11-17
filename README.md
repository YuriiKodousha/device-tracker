# Device Tracker

Daily log of devices: statuses (each status colors the whole row), "handed over" checkbox,
statistics per day and for all time, search by number, and a **Polish Text Corrector** window.
Runs on **Windows** and **Linux**. No extra libraries needed (Python standard library only).

## Run

**Windows:** see the step-by-step guide **[INSTALL_WINDOWS.md](INSTALL_WINDOWS.md)** (in Ukrainian). Short version: install Python from https://www.python.org (tick "Add python.exe to PATH"), double-click `run_windows.bat`, optionally `create_shortcut_windows.bat`.
To get a single `DeviceTracker.exe` that runs without Python, run `build_windows.bat` (result in `dist`).

**Linux:** `./run_linux.sh` (if tkinter is missing: `sudo apt install python3-tk`).
Single executable: `./build_linux.sh` → `dist/DeviceTracker`.

> The .exe must be built on Windows and the Linux binary on Linux.

## Where data is stored

- Windows: `%APPDATA%\DeviceTracker\devices.db`
- Linux: `~/.local/share/DeviceTracker/devices.db`

The path is shown at the bottom of the window. A backup copy is made every day in `backups` (last 30 kept).
To move data to another computer, copy `devices.db`.

## Device log

- **Today** opens (or creates) today's day. **+ New day...** for any date.
- Type a number → choose status → **Enter**. Works with a barcode scanner too.
  If the number was seen on earlier days, the status bar shows when and with what status.
- Columns: # · Time · Device number · Status · Note · **Handed** (big checkbox on the right — click it). **Space** marks all selected rows.
- **Ctrl+F** jumps to the search box (works with any keyboard layout); type a number, **Enter** jumps to the first result.
- **1–9** set the status of selected rows. Right-click: change status, edit, delete, go to day.
- **Search**: inside a day it searches that day; in "All days" it searches everything. Days with matches turn yellow.
- **Statuses...**: create your own statuses — type a name, click a color (or *Custom color...*), *Add as new*.
  Click an existing status to rename/recolor it, ▲/▼ to reorder (order = keys 1–9).
- **☀ Light / ☾ Dark** switches the theme (remembered).
- **More**: Export CSV, open data folder, add/remove demo data (demo records are marked and removed without touching yours).
- Click a column header to sort. Exported CSV opens in Excel.

## Polish Text Corrector

Button **Polish Corrector** opens a separate window: paste text on the left → **Correct** (Ctrl+Enter) →
corrected text on the right with changes highlighted, plus a list of changes → **Copy result**.

Engines:
- **LanguageTool** (default) — free, no account or key. Fixes spelling, diacritics, grammar, punctuation.
  Uses the public server api.languagetool.org (limit ~20 requests/minute, ~18 000 characters per text).
- **AI model** — better at natural phrasing. Open **AI settings...**, choose a preset, paste your key:
  - Groq — free tier, key at https://console.groq.com/keys
  - Google Gemini — free tier, key at https://aistudio.google.com/apikey
  - OpenRouter — has free models, key at https://openrouter.ai/keys
  - Ollama — runs locally on your PC, offline, no key (https://ollama.com)

  Model names change over time; if you get "model not found", enter a current model name from the provider's list.

Text you correct is sent to the selected service over the internet (except Ollama).
# device-tracker
