#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Device Tracker - daily log of devices with statuses + Polish text corrector.

Runs on Windows and Linux (Python 3.8+, standard library only: tkinter + sqlite3 + urllib).
Data is stored in an SQLite database:
  Windows: %APPDATA%\\DeviceTracker\\devices.db
  Linux:   ~/.local/share/DeviceTracker/devices.db
A daily backup copy is made on start in the "backups" folder (last 30 kept).
"""

import csv
import datetime as dt
import difflib
import glob
import json
import os
import queue
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont

APP_NAME = "DeviceTracker"
APP_TITLE = "Device Tracker"
APP_VERSION = "1.1"
BACKUPS_TO_KEEP = 30

# Default statuses - can be changed in the app ("Statuses..." button).
DEFAULT_STATUSES = [
    ("Working", "#b7e4c7"),
    ("Needs repair", "#ffe08a"),
    ("Waiting for parts", "#ffc078"),
    ("No parts available", "#ffa8a8"),
    ("Not checked", "#a5d8ff"),
    ("Write-off", "#ced4da"),
]
# Defaults from version 1.0 (Ukrainian) -> renamed automatically.
OLD_DEFAULT_NAMES = {
    "Справний": "Working",
    "Потребує ремонту": "Needs repair",
    "Очікує запчастини": "Waiting for parts",
    "Немає запчастин": "No parts available",
    "Не перевірено": "Not checked",
    "На списання": "Write-off",
}

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Colors offered in the status editor (any other color can be picked with "Custom color...").
PALETTE = [
    "#ffadad", "#ffd6a5", "#fdffb6", "#caffbf", "#9bf6ff", "#a0c4ff", "#bdb2ff", "#ffc6ff", "#e9ecef",
    "#ff6b6b", "#ffa94d", "#ffd43b", "#69db7c", "#3bc9db", "#4dabf7", "#9775fa", "#f783ac", "#adb5bd",
]

THEMES = {
    "light": {
        "bg": "#f3f3f3", "panel": "#e6e6e6", "field": "#ffffff", "fg": "#1f1f1f", "muted": "#666666",
        "warn": "#b03030", "border": "#c4c4c4", "heading": "#e1e1e1",
        "button": "#e4e4e4", "button_active": "#d4d4d4",
        "accent": "#2f6fd6", "accent_active": "#285fb8", "accent_fg": "#ffffff",
        "select": "#2f6fd6", "select_fg": "#ffffff",
        "all_bg": "#e7eefc", "match_bg": "#fff3bf", "match_fg": "#1f1f1f", "changed": "#fff3a0",
        "row_mix": 1.0,          # how strongly status colors are used for rows (1 = as chosen)
    },
    "dark": {
        "bg": "#1e1f22", "panel": "#2b2d31", "field": "#25272b", "fg": "#e3e5e8", "muted": "#9aa0a6",
        "warn": "#ff8a8a", "border": "#3f4248", "heading": "#2f3136",
        "button": "#383a40", "button_active": "#46494f",
        "accent": "#3d7bf0", "accent_active": "#5a90f5", "accent_fg": "#ffffff",
        "select": "#3d6fd9", "select_fg": "#ffffff",
        "all_bg": "#2c3a55", "match_bg": "#5a4b12", "match_fg": "#ffe9a0", "changed": "#6b5a10",
        "row_mix": 0.5,          # status colors are mixed with the dark background so they don't glare
    },
}

DEMO_NOTES = ["", "", "", "", "Screen cracked", "Battery swollen", "No power", "Missing back cover",
              "Fan noise", "Charging port damaged", "Keyboard replaced", "Waiting for display",
              "Customer complaint", "Water damage", "Firmware updated", "Ekran pęknięty", "Brak zasilania"]

# Polish corrector
LANGUAGETOOL_URL = "https://api.languagetool.org/v2/check"
PROVIDER_LT = "LanguageTool (free, no key)"
PROVIDER_AI = "AI model (OpenAI-compatible API)"
AI_PRESETS = {
    # name: (base URL, model, needs key, where to get key)
    "Groq (free tier)": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", True,
                         "https://console.groq.com/keys"),
    "Google Gemini (free tier)": ("https://generativelanguage.googleapis.com/v1beta/openai",
                                  "gemini-2.5-flash", True, "https://aistudio.google.com/apikey"),
    "OpenRouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free", True,
                   "https://openrouter.ai/keys"),
    "Ollama (local, offline)": ("http://localhost:11434/v1", "llama3.1", False, "https://ollama.com"),
}
AI_SYSTEM_PROMPT = (
    "You are a professional Polish proofreader. Correct spelling, grammar, punctuation and missing "
    "Polish diacritics (ą, ć, ę, ł, ń, ó, ś, ź, ż) in the user's Polish text. Keep the original meaning, "
    "tone, line breaks and formatting. Do not translate and do not add anything. "
    "Return ONLY the corrected text, without quotes, comments or explanations."
)
HTTP_HEADERS = {"User-Agent": "DeviceTracker/%s" % APP_VERSION, "Accept": "application/json"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS statuses(
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE,
    color TEXT NOT NULL,
    sort  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS days(
    id   INTEGER PRIMARY KEY,
    date TEXT NOT NULL UNIQUE,         -- YYYY-MM-DD
    demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS devices(
    id        INTEGER PRIMARY KEY,
    day_id    INTEGER NOT NULL REFERENCES days(id) ON DELETE CASCADE,
    serial    TEXT NOT NULL,
    status_id INTEGER REFERENCES statuses(id) ON DELETE SET NULL,
    note      TEXT NOT NULL DEFAULT '',
    done      INTEGER NOT NULL DEFAULT 0,
    created   TEXT NOT NULL,
    demo      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings(
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_devices_day ON devices(day_id);
CREATE INDEX IF NOT EXISTS idx_devices_serial ON devices(serial);
"""


# ----------------------------------------------------------------------------- helpers

def data_dir():
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def make_backup(db_path):
    """One copy of the database per day, keep the last BACKUPS_TO_KEEP."""
    if not os.path.exists(db_path):
        return
    bdir = os.path.join(os.path.dirname(db_path), "backups")
    os.makedirs(bdir, exist_ok=True)
    target = os.path.join(bdir, "devices_%s.db" % dt.date.today().isoformat())
    if not os.path.exists(target):
        try:
            shutil.copy2(db_path, target)
        except OSError:
            pass
    old = sorted(glob.glob(os.path.join(bdir, "devices_*.db")))
    for f in old[:-BACKUPS_TO_KEEP]:
        try:
            os.remove(f)
        except OSError:
            pass


def fmt_date(iso):
    try:
        return dt.date.fromisoformat(iso).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return iso or ""


def parse_date(text):
    text = (text or "").strip()
    for f in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(text, f).date()
        except ValueError:
            continue
    return None


def text_color(bg):
    try:
        h = bg.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return "#000000"
    return "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else "#ffffff"


UI_SCALE = 1.0      # set from the screen DPI at start (Windows 125% / 150% etc.)


def px(n):
    """Pixels scaled for the screen DPI."""
    return int(round(n * UI_SCALE))


def fit_window(win, w, h, min_w, min_h):
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    w, h = min(px(w), sw - 40), min(px(h), sh - 90)
    win.geometry("%dx%d" % (w, h))
    win.minsize(min(px(min_w), w), min(px(min_h), h))


def set_dark_title_bar(win, dark):
    """Windows 10/11: dark window title bar to match the dark theme."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):    # DWMWA_USE_IMMERSIVE_DARK_MODE (new / old Windows 10 builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value),
                                                          ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


def log_error(text):
    try:
        with open(os.path.join(data_dir(), "error.log"), "a", encoding="utf-8") as f:
            f.write("\n[%s]\n%s\n" % (dt.datetime.now().isoformat(timespec="seconds"), text))
    except OSError:
        pass


def mix_colors(color, base, amount):
    """amount=1 -> color, amount=0 -> base."""
    try:
        c = [int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
        b = [int(base.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    except (ValueError, IndexError):
        return color
    return "#%02x%02x%02x" % tuple(round(x * amount + y * (1 - amount)) for x, y in zip(c, b))


# ----------------------------------------------------------------------------- database

class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        for table in ("days", "devices"):     # upgrade databases from older versions
            cols = [r[1] for r in self.conn.execute("PRAGMA table_info(%s)" % table)]
            if "demo" not in cols:
                self.conn.execute("ALTER TABLE %s ADD COLUMN demo INTEGER NOT NULL DEFAULT 0" % table)
        if not self.conn.execute("SELECT 1 FROM statuses LIMIT 1").fetchone():
            self.conn.executemany(
                "INSERT INTO statuses(name, color, sort) VALUES(?,?,?)",
                [(n, c, i) for i, (n, c) in enumerate(DEFAULT_STATUSES)],
            )
        else:
            for old, new in OLD_DEFAULT_NAMES.items():
                try:
                    self.conn.execute("UPDATE statuses SET name=? WHERE name=?", (new, old))
                except sqlite3.IntegrityError:
                    pass
        self.conn.commit()

    def q(self, sql, args=()):
        return self.conn.execute(sql, args).fetchall()

    def w(self, sql, args=()):
        cur = self.conn.execute(sql, args)
        self.conn.commit()
        return cur.lastrowid

    # settings
    def get_setting(self, key, default=""):
        row = self.q("SELECT value FROM settings WHERE key=?", (key,))
        return row[0]["value"] if row else default

    def set_setting(self, key, value):
        self.w("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)", (key, value))

    # statuses
    def statuses(self):
        return self.q("SELECT * FROM statuses ORDER BY sort, id")

    def add_status(self, name, color):
        mx = self.q("SELECT COALESCE(MAX(sort), -1) AS m FROM statuses")[0]["m"]
        return self.w("INSERT INTO statuses(name, color, sort) VALUES(?,?,?)", (name, color, mx + 1))

    def update_status(self, sid, name=None, color=None):
        if name is not None:
            self.w("UPDATE statuses SET name=? WHERE id=?", (name, sid))
        if color is not None:
            self.w("UPDATE statuses SET color=? WHERE id=?", (color, sid))

    def status_usage(self, sid):
        return self.q("SELECT COUNT(*) AS c FROM devices WHERE status_id=?", (sid,))[0]["c"]

    def delete_status(self, sid):
        self.w("UPDATE devices SET status_id=NULL WHERE status_id=?", (sid,))
        self.w("DELETE FROM statuses WHERE id=?", (sid,))

    def reorder_statuses(self, ids):
        self.conn.executemany("UPDATE statuses SET sort=? WHERE id=?", [(i, sid) for i, sid in enumerate(ids)])
        self.conn.commit()

    # days
    def days(self, search=""):
        return self.q(
            """
            SELECT d.id, d.date,
                   COUNT(v.id)   AS total,
                   TOTAL(v.done) AS done,
                   TOTAL(CASE WHEN ? <> '' AND instr(lower(v.serial), lower(?)) > 0 THEN 1 ELSE 0 END) AS found
            FROM days d LEFT JOIN devices v ON v.day_id = d.id
            GROUP BY d.id ORDER BY d.date DESC
            """,
            (search, search),
        )

    def get_or_create_day(self, date):
        iso = date.isoformat()
        row = self.q("SELECT id FROM days WHERE date=?", (iso,))
        if row:
            return row[0]["id"]
        return self.w("INSERT INTO days(date) VALUES(?)", (iso,))

    def day_by_date(self, date):
        row = self.q("SELECT id FROM days WHERE date=?", (date.isoformat(),))
        return row[0]["id"] if row else None

    def day_date(self, day_id):
        row = self.q("SELECT date FROM days WHERE id=?", (day_id,))
        return row[0]["date"] if row else None

    def delete_day(self, day_id):
        self.w("DELETE FROM days WHERE id=?", (day_id,))

    # devices
    def devices(self, day_id=None, search=""):
        return self.q(
            """
            SELECT v.*, d.date, s.name AS sname, s.color
            FROM devices v
            JOIN days d ON d.id = v.day_id
            LEFT JOIN statuses s ON s.id = v.status_id
            WHERE (? IS NULL OR v.day_id = ?)
              AND (? = '' OR instr(lower(v.serial), lower(?)) > 0)
            ORDER BY d.date DESC, v.id
            """,
            (day_id, day_id, search, search),
        )

    def device(self, dev_id):
        rows = self.q(
            """SELECT v.*, d.date, s.name AS sname FROM devices v JOIN days d ON d.id=v.day_id
               LEFT JOIN statuses s ON s.id=v.status_id WHERE v.id=?""",
            (dev_id,),
        )
        return rows[0] if rows else None

    def find_serial(self, serial):
        return self.q(
            """SELECT v.day_id, d.date, s.name AS sname FROM devices v JOIN days d ON d.id=v.day_id
               LEFT JOIN statuses s ON s.id=v.status_id
               WHERE lower(v.serial) = lower(?) ORDER BY d.date DESC""",
            (serial,),
        )

    def add_device(self, day_id, serial, status_id, note=""):
        now = dt.datetime.now().isoformat(timespec="seconds")
        return self.w(
            "INSERT INTO devices(day_id, serial, status_id, note, done, created) VALUES(?,?,?,?,0,?)",
            (day_id, serial, status_id, note, now),
        )

    def update_device(self, dev_id, serial, status_id, note, done):
        self.w(
            "UPDATE devices SET serial=?, status_id=?, note=?, done=? WHERE id=?",
            (serial, status_id, note, 1 if done else 0, dev_id),
        )

    def set_done(self, ids, done):
        self.conn.executemany("UPDATE devices SET done=? WHERE id=?", [(1 if done else 0, i) for i in ids])
        self.conn.commit()

    def set_status(self, ids, status_id):
        self.conn.executemany("UPDATE devices SET status_id=? WHERE id=?", [(status_id, i) for i in ids])
        self.conn.commit()

    def delete_devices(self, ids):
        self.conn.executemany("DELETE FROM devices WHERE id=?", [(i,) for i in ids])
        self.conn.commit()

    # demo data
    def add_demo_data(self, status_ids, n_days=10):
        rnd = random.Random()
        today = dt.date.today()
        now = dt.datetime.now()
        days, d = [], today
        while len(days) < n_days:
            if d.weekday() < 5 or d == today:
                days.append(d)
            d -= dt.timedelta(days=1)
        pool = ["%s-%06d" % (rnd.choice(["SN", "TX", "PL", "DV"]), rnd.randint(100000, 999999))
                for _ in range(130)]
        weights = ([6, 4, 3, 2, 2, 1] + [1] * len(status_ids))[:len(status_ids)]
        total = 0
        for day in reversed(days):
            day_id = self.day_by_date(day)
            if day_id is None:
                day_id = self.conn.execute("INSERT INTO days(date, demo) VALUES(?, 1)",
                                           (day.isoformat(),)).lastrowid
            count = rnd.randint(9, 22)
            if day == today:
                times = sorted(now - dt.timedelta(minutes=rnd.randint(0, 300)) for _ in range(count))
            else:
                start = dt.datetime.combine(day, dt.time(8, 0))
                times = sorted(start + dt.timedelta(minutes=rnd.randint(0, 540)) for _ in range(count))
            for t in times:
                done = rnd.random() < (0.3 if day == today else 0.85)
                self.conn.execute(
                    "INSERT INTO devices(day_id, serial, status_id, note, done, created, demo) "
                    "VALUES(?,?,?,?,?,?,1)",
                    (day_id, rnd.choice(pool), rnd.choices(status_ids, weights)[0] if status_ids else None,
                     rnd.choice(DEMO_NOTES), 1 if done else 0, t.isoformat(timespec="seconds")))
                total += 1
        self.conn.commit()
        return total

    def demo_count(self):
        return self.q("SELECT COUNT(*) AS c FROM devices WHERE demo=1")[0]["c"]

    def remove_demo_data(self):
        n = self.demo_count()
        self.conn.execute("DELETE FROM devices WHERE demo=1")
        self.conn.execute("DELETE FROM days WHERE demo=1 AND id NOT IN (SELECT day_id FROM devices)")
        self.conn.commit()
        return n

    def stats(self, day_id=None):
        rows = self.q(
            """SELECT status_id, COUNT(*) AS c, TOTAL(done) AS d FROM devices
               WHERE (? IS NULL OR day_id = ?) GROUP BY status_id""",
            (day_id, day_id),
        )
        by = {r["status_id"]: (r["c"], int(r["d"])) for r in rows}
        total = sum(c for c, _ in by.values())
        done = sum(d for _, d in by.values())
        return by, total, done


# ----------------------------------------------------------------------------- polish correction (network)

def _utf16_to_index(text, offsets):
    """LanguageTool offsets are UTF-16 code units; convert them to Python string indices."""
    wanted = sorted(set(offsets))
    mapping, u16, wi = {}, 0, 0
    for i, ch in enumerate(text):
        while wi < len(wanted) and wanted[wi] <= u16:
            mapping[wanted[wi]] = i
            wi += 1
        u16 += 2 if ord(ch) > 0xFFFF else 1
    for o in wanted[wi:]:
        mapping[o] = len(text)
    return mapping


def http_json(url, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers=dict(HTTP_HEADERS, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        body = ex.read().decode("utf-8", "replace")
        try:
            j = json.loads(body)
            if isinstance(j, list) and j:
                j = j[0]
            err = j.get("error") if isinstance(j, dict) else None
            msg = err.get("message") if isinstance(err, dict) else err
            body = msg or body
        except (ValueError, AttributeError):
            pass
        raise RuntimeError("HTTP %d: %s" % (ex.code, str(body)[:500]))
    except urllib.error.URLError as ex:
        raise RuntimeError("Connection error: %s\nCheck your internet connection." % ex.reason)


def correct_languagetool(text):
    """Returns (corrected_text, [(original, replacement, explanation), ...])."""
    data = urllib.parse.urlencode({"text": text, "language": "pl-PL"}).encode("utf-8")
    res = http_json(LANGUAGETOOL_URL, data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
    matches = [m for m in res.get("matches", []) if m.get("replacements")]
    offs = []
    for m in matches:
        offs += [m["offset"], m["offset"] + m["length"]]
    idx = _utf16_to_index(text, offs)
    edits = sorted(
        ((idx[m["offset"]], idx[m["offset"] + m["length"]], m["replacements"][0]["value"], m.get("message", ""))
         for m in matches),
        key=lambda e: e[0])
    out, changes, pos = [], [], 0
    for a, b, rep, msg in edits:
        if a < pos:            # overlapping suggestion - skip
            continue
        out.append(text[pos:a])
        out.append(rep)
        changes.append((text[a:b], rep, msg))
        pos = b
    out.append(text[pos:])
    return "".join(out), changes


def correct_ai(text, base_url, model, api_key):
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": AI_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    res = http_json(url, data=body, headers=headers, timeout=120)
    try:
        out = res["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Unexpected response: %s" % json.dumps(res)[:500])
    out = (out or "").strip()
    # some models wrap the answer in code fences or quotes
    if out.startswith("```") and out.endswith("```") and len(out) > 6:
        out = out[3:-3]
        if "\n" in out:
            first, rest = out.split("\n", 1)
            if first.strip().isalpha() or not first.strip():
                out = rest
        out = out.strip()
    if len(out) >= 2 and out[0] == out[-1] and out[0] in "\"'" and text.strip()[:1] not in "\"'„":
        out = out[1:-1]
    return out, None


TOKEN_RE = re.compile(r"\s+|\w+|[^\w\s]", re.UNICODE)


def diff_changes(old, new):
    """Word-level diff: returns ([(start, end) ranges in new text], [(old_part, new_part), ...])."""
    a = TOKEN_RE.findall(old)
    b = TOKEN_RE.findall(new)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    pos_b = [0]
    for t in b:
        pos_b.append(pos_b[-1] + len(t))
    ranges, pairs = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        o, n = "".join(a[i1:i2]), "".join(b[j1:j2])
        if not o.strip() and not n.strip():
            continue
        if j2 > j1:
            ranges.append((pos_b[j1], pos_b[j2]))
        pairs.append((o.strip() or "(nothing)", n.strip() or "(removed)"))
    return ranges, pairs


# ----------------------------------------------------------------------------- dialogs

class Modal(tk.Toplevel):
    def __init__(self, parent, title):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        self.bind("<Escape>", lambda e: self.destroy())
        set_dark_title_bar(self, getattr(parent.winfo_toplevel(), "theme", None) == "dark"
                           or getattr(getattr(parent, "app", None), "theme", None) == "dark")

    def show(self):
        self.update_idletasks()
        p = self.master
        x = p.winfo_rootx() + (p.winfo_width() - self.winfo_reqwidth()) // 2
        y = p.winfo_rooty() + (p.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry("+%d+%d" % (max(x, 0), max(y, 0)))
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass
        self.wait_window()
        return self.result


class DeviceDialog(Modal):
    def __init__(self, app, row):
        super().__init__(app, "Edit record")
        self.app = app
        f = ttk.Frame(self, padding=14)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="Date:").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Label(f, text=fmt_date(row["date"])).grid(row=0, column=1, sticky="w", pady=4)

        ttk.Label(f, text="Number:").grid(row=1, column=0, sticky="w", pady=4)
        self.serial = tk.StringVar(value=row["serial"])
        e = ttk.Entry(f, textvariable=self.serial, width=36)
        e.grid(row=1, column=1, sticky="we", pady=4)

        ttk.Label(f, text="Status:").grid(row=2, column=0, sticky="w", pady=4)
        names = [s["name"] for s in app.statuses]
        self.status = tk.StringVar(value=row["sname"] or "")
        ttk.Combobox(f, textvariable=self.status, values=names, state="readonly", width=34).grid(
            row=2, column=1, sticky="we", pady=4)

        ttk.Label(f, text="Note:").grid(row=3, column=0, sticky="w", pady=4)
        self.note = tk.StringVar(value=row["note"])
        ttk.Entry(f, textvariable=self.note, width=36).grid(row=3, column=1, sticky="we", pady=4)

        self.done = tk.BooleanVar(value=bool(row["done"]))
        ttk.Checkbutton(f, text="Handed over", variable=self.done).grid(row=4, column=1, sticky="w", pady=6)

        b = ttk.Frame(f)
        b.grid(row=5, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(b, text="Save", command=self.ok).pack(side="right", padx=4)
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="right")
        self.bind("<Return>", lambda ev: self.ok())
        e.focus_set()
        e.select_range(0, "end")

    def ok(self):
        serial = self.serial.get().strip()
        if not serial:
            messagebox.showwarning("Error", "Number cannot be empty.", parent=self)
            return
        self.result = (serial, self.app.status_id_by_name(self.status.get()), self.note.get().strip(),
                       self.done.get())
        self.destroy()


class StatusesDialog(Modal):
    """Create your own statuses: name + color, order, delete."""

    def __init__(self, app):
        super().__init__(app, "Statuses")
        self.app = app
        c = app.c
        self.color = PALETTE[5]
        self.resizable(True, True)
        f = ttk.Frame(self, padding=14)
        f.pack(fill="both", expand=True)
        ttk.Label(f, style="Muted.TLabel",
                  text="Click a status to change its name or color. To create a new one, type a name, "
                       "pick a color and press \"Add as new\".").grid(row=0, column=0, columnspan=2,
                                                                       sticky="w", pady=(0, 10))

        # --- list
        left = ttk.Frame(f)
        left.grid(row=1, column=0, sticky="nsew")
        self.tree = ttk.Treeview(left, columns=("n", "name", "used"), show="headings", height=14,
                                 selectmode="browse")
        self.tree.heading("n", text="Key")
        self.tree.heading("name", text="Status")
        self.tree.heading("used", text="Records")
        self.tree.column("n", width=px(50), anchor="center", stretch=False)
        self.tree.column("name", width=px(240))
        self.tree.column("used", width=px(80), anchor="center", stretch=False)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        lb = ttk.Frame(left)
        lb.pack(fill="x", pady=(6, 0))
        ttk.Button(lb, text="▲ Up", command=lambda: self.move(-1)).pack(side="left")
        ttk.Button(lb, text="▼ Down", command=lambda: self.move(1)).pack(side="left", padx=4)
        ttk.Button(lb, text="Delete", command=self.delete).pack(side="right")

        # --- editor
        right = ttk.Frame(f)
        right.grid(row=1, column=1, sticky="n", padx=(16, 0))
        ttk.Label(right, text="Name:", style="Bold.TLabel").pack(anchor="w")
        self.name = tk.StringVar()
        self.name_entry = ttk.Entry(right, textvariable=self.name, width=32)
        self.name_entry.pack(fill="x", pady=(2, 12))
        self.name_entry.bind("<Return>", lambda e: self.save())

        ttk.Label(right, text="Color:", style="Bold.TLabel").pack(anchor="w")
        pal = tk.Frame(right, bg=c["bg"])
        pal.pack(anchor="w", pady=(4, 4))
        self.swatches = {}
        for i, col in enumerate(PALETTE):
            sw = tk.Label(pal, bg=col, width=3, height=1, cursor="hand2", highlightthickness=3,
                          highlightbackground=c["bg"], highlightcolor=c["bg"])
            sw.grid(row=i // 9, column=i % 9, padx=1, pady=1)
            sw.bind("<Button-1>", lambda e, col=col: self.pick(col))
            self.swatches[col] = sw
        ttk.Button(right, text="Custom color...", command=self.custom).pack(anchor="w", pady=(4, 12))

        ttk.Label(right, text="How the row will look:", style="Bold.TLabel").pack(anchor="w")
        self.preview = tk.Label(right, text="", anchor="w", padx=10, pady=6, font=app.font_default)
        self.preview.pack(fill="x", pady=(4, 14))

        bf = ttk.Frame(right)
        bf.pack(fill="x")
        ttk.Button(bf, text="Save changes", style="Accent.TButton", command=self.save).pack(side="left")
        ttk.Button(bf, text="Add as new", command=self.add).pack(side="left", padx=6)
        ttk.Button(right, text="Close", command=self.destroy).pack(anchor="e", pady=(24, 0))

        ttk.Label(f, style="Muted.TLabel",
                  text="Keys 1-9 in the table set the status with that number. Order here = order everywhere."
                  ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        self.name.trace_add("write", lambda *a: self.update_preview())
        self.fill()
        first = self.tree.get_children()
        if first:
            self.tree.selection_set(first[0])
        else:
            self.update_preview()
        self.name_entry.focus_set()

    # helpers
    def fill(self, select=None):
        self.tree.delete(*self.tree.get_children())
        for i, s in enumerate(self.app.db.statuses(), 1):
            tag = "c%d" % s["id"]
            bg = self.app.row_color(s["color"])
            self.tree.tag_configure(tag, background=bg, foreground=text_color(bg))
            self.tree.insert("", "end", iid=str(s["id"]), tags=(tag,),
                             values=(i if i <= 9 else "", s["name"], self.app.db.status_usage(s["id"])))
        if select is not None and self.tree.exists(str(select)):
            self.tree.selection_set(str(select))
            self.tree.see(str(select))

    def selected(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def status(self, sid):
        return next((s for s in self.app.db.statuses() if s["id"] == sid), None)

    def changed(self, select=None):
        self.fill(select)
        self.app.on_statuses_changed()

    def on_select(self, _e=None):
        s = self.status(self.selected())
        if s:
            self.name.set(s["name"])
            self.pick(s["color"])

    def pick(self, color):
        self.color = color
        for col, sw in self.swatches.items():
            mark = self.app.c["fg"] if col.lower() == color.lower() else self.app.c["bg"]
            sw.configure(highlightbackground=mark, highlightcolor=mark)
        self.update_preview()

    def custom(self):
        rgb, _ = colorchooser.askcolor(color=self.color, parent=self, title="Status color")
        if rgb:
            self.pick("#%02x%02x%02x" % tuple(int(v) for v in rgb))

    def update_preview(self):
        bg = self.app.row_color(self.color)
        self.preview.configure(text="☐   SN-123456    %s" % (self.name.get().strip() or "Status name"),
                               bg=bg, fg=text_color(bg))

    # actions
    def save(self):
        sid = self.selected()
        name = self.name.get().strip()
        if sid is None:
            return self.add()
        if not name:
            messagebox.showwarning("Error", "Name cannot be empty.", parent=self)
            return
        try:
            self.app.db.update_status(sid, name=name, color=self.color)
        except sqlite3.IntegrityError:
            messagebox.showwarning("Error", "A status with this name already exists.", parent=self)
            return
        self.changed(sid)

    def add(self):
        name = self.name.get().strip()
        if not name:
            messagebox.showwarning("Error", "Type a name for the new status first.", parent=self)
            self.name_entry.focus_set()
            return
        try:
            sid = self.app.db.add_status(name, self.color)
        except sqlite3.IntegrityError:
            messagebox.showwarning("Error", "A status with this name already exists.\n"
                                            "Change the name to add a new one.", parent=self)
            return
        self.changed(sid)

    def move(self, delta):
        sid = self.selected()
        if sid is None:
            return
        ids = [s["id"] for s in self.app.db.statuses()]
        i = ids.index(sid)
        j = i + delta
        if not 0 <= j < len(ids):
            return
        ids[i], ids[j] = ids[j], ids[i]
        self.app.db.reorder_statuses(ids)
        self.changed(sid)

    def delete(self):
        sid = self.selected()
        s = self.status(sid)
        if not s:
            return
        used = self.app.db.status_usage(sid)
        msg = "Delete status \"%s\"?" % s["name"]
        if used:
            msg += "\n\n%d records use it - they will be left without a status." % used
        if messagebox.askyesno("Delete status", msg, parent=self):
            self.app.db.delete_status(sid)
            self.changed()
            first = self.tree.get_children()
            if first:
                self.tree.selection_set(first[0])


class AISettingsDialog(Modal):
    def __init__(self, parent, db):
        super().__init__(parent, "AI settings")
        self.db = db
        f = ttk.Frame(self, padding=14)
        f.pack(fill="both", expand=True)
        default_base, default_model = AI_PRESETS["Groq (free tier)"][:2]

        ttk.Label(f, text="Preset:").grid(row=0, column=0, sticky="w", pady=4)
        self.preset = tk.StringVar()
        cb = ttk.Combobox(f, textvariable=self.preset, values=list(AI_PRESETS), state="readonly", width=44)
        cb.grid(row=0, column=1, sticky="we", pady=4)
        cb.bind("<<ComboboxSelected>>", self.apply_preset)

        self.base = tk.StringVar(value=db.get_setting("ai_base_url", default_base))
        self.model = tk.StringVar(value=db.get_setting("ai_model", default_model))
        self.key = tk.StringVar(value=db.get_setting("ai_api_key", ""))
        ttk.Label(f, text="Base URL:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.base, width=46).grid(row=1, column=1, sticky="we", pady=4)
        ttk.Label(f, text="Model:").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.model, width=46).grid(row=2, column=1, sticky="we", pady=4)
        ttk.Label(f, text="API key:").grid(row=3, column=0, sticky="w", pady=4)
        self.key_entry = ttk.Entry(f, textvariable=self.key, width=46, show="•")
        self.key_entry.grid(row=3, column=1, sticky="we", pady=4)

        self.show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Show key", variable=self.show_key,
                        command=lambda: self.key_entry.configure(show="" if self.show_key.get() else "•")
                        ).grid(row=4, column=1, sticky="w")

        ttk.Label(f, style="Muted.TLabel", wraplength=px(440), justify="left", text=(
            "Groq and Google Gemini have free tiers - create a key on their website and paste it here. "
            "Ollama runs on your own computer (no key, no internet needed). "
            "Model names change over time - if you get a 'model not found' error, pick a current model "
            "from the provider's list.\n"
            "The key is stored only on this computer, in the app database.")
                  ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 4))
        self.link = ttk.Entry(f, width=46)          # selectable, so the URL can be copied
        self.link.grid(row=6, column=0, columnspan=2, sticky="we")
        self.link.insert(0, "Choose a preset to see where to get a key.")
        self.link.state(["readonly"])

        b = ttk.Frame(f)
        b.grid(row=7, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(b, text="Save", command=self.ok).pack(side="right", padx=4)
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="right")

    def apply_preset(self, _e=None):
        base, model, needs_key, where = AI_PRESETS[self.preset.get()]
        self.base.set(base)
        self.model.set(model)
        self.link.state(["!readonly"])
        self.link.delete(0, "end")
        self.link.insert(0, ("Get a free key at: " if needs_key else "Download from: ") + where)
        self.link.state(["readonly"])

    def ok(self):
        self.db.set_setting("ai_base_url", self.base.get().strip())
        self.db.set_setting("ai_model", self.model.get().strip())
        self.db.set_setting("ai_api_key", self.key.get().strip())
        self.result = True
        self.destroy()


# ----------------------------------------------------------------------------- polish corrector window

def add_text_bindings(widget):
    """Right-click menu and Ctrl+A for tk.Text (Linux Tk has neither by default)."""
    def select_all(_e=None):
        widget.tag_add("sel", "1.0", "end-1c")
        widget.mark_set("insert", "end-1c")
        return "break"

    def popup(e):
        m = tk.Menu(widget, tearoff=0)
        m.add_command(label="Cut", command=lambda: widget.event_generate("<<Cut>>"))
        m.add_command(label="Copy", command=lambda: widget.event_generate("<<Copy>>"))
        m.add_command(label="Paste", command=lambda: widget.event_generate("<<Paste>>"))
        m.add_separator()
        m.add_command(label="Select all", command=select_all)
        m.tk_popup(e.x_root, e.y_root)

    widget.bind("<Control-a>", select_all)
    widget.bind("<Control-A>", select_all)
    widget.bind("<Button-3>", popup)
    if sys.platform == "darwin":
        widget.bind("<Button-2>", popup)


class CorrectorWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.db = app.db
        self.title("Polish Text Corrector")
        fit_window(self, 1100, 720, 760, 480)
        self.results = queue.Queue()
        self.busy = False

        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill="x")
        ttk.Label(top, text="Engine:").pack(side="left")
        self.provider = tk.StringVar(value=self.db.get_setting("corrector_provider", PROVIDER_LT))
        cb = ttk.Combobox(top, textvariable=self.provider, values=[PROVIDER_LT, PROVIDER_AI],
                          state="readonly", width=34)
        cb.pack(side="left", padx=(6, 6))
        cb.bind("<<ComboboxSelected>>", self.on_provider)
        self.settings_btn = ttk.Button(top, text="AI settings...", command=self.open_settings)
        self.settings_btn.pack(side="left")
        self.engine_info = ttk.Label(top, style="Muted.TLabel")
        self.engine_info.pack(side="left", padx=10)

        # bottom parts are packed first so they always stay visible
        self.status = ttk.Label(self, text="Paste Polish text on the left and press Correct.",
                                style="Status.TLabel", anchor="w", padding=(8, 4))
        self.status.pack(fill="x", side="bottom")

        lf = ttk.LabelFrame(self, text="Changes", padding=6)
        lf.pack(fill="x", side="bottom", padx=10, pady=(0, 6))
        self.changes = ttk.Treeview(lf, columns=("old", "new", "why"), show="headings", height=6)
        for c, t, w, s in (("old", "Original", 200, False), ("new", "Correction", 200, False),
                           ("why", "Explanation", 400, True)):
            self.changes.heading(c, text=t)
            self.changes.column(c, width=px(w), stretch=s)
        csb = ttk.Scrollbar(lf, orient="vertical", command=self.changes.yview)
        self.changes.configure(yscrollcommand=csb.set)
        self.changes.pack(side="left", fill="both", expand=True)
        csb.pack(side="left", fill="y")

        btns = ttk.Frame(self, padding=(10, 6))
        btns.pack(fill="x", side="bottom")
        ttk.Button(btns, text="Paste", command=self.paste).pack(side="left")
        ttk.Button(btns, text="Clear", command=self.clear).pack(side="left", padx=4)
        self.go_btn = ttk.Button(btns, text="Correct  (Ctrl+Enter)", style="Accent.TButton",
                                 command=self.correct)
        self.go_btn.pack(side="left", padx=12)
        self.progress = ttk.Progressbar(btns, mode="indeterminate", length=120)
        ttk.Button(btns, text="Copy result", style="Accent.TButton", command=self.copy_result).pack(side="right")
        ttk.Button(btns, text="Use result as original", command=self.use_result).pack(side="right", padx=4)

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10)
        self.src = self._text_panel(body, "Original text (paste here):")
        self.dst = self._text_panel(body, "Corrected text (changes highlighted):")
        self.apply_theme()

        self.bind("<Control-Return>", self._ctrl_enter)
        self.bind("<Control-KP_Enter>", self._ctrl_enter)
        self.on_provider()
        self.src.focus_set()
        self.after(100, self.poll)

    def apply_theme(self):
        c = self.app.c
        self.configure(bg=c["bg"])
        set_dark_title_bar(self, self.app.theme == "dark")
        for t in (self.src, self.dst):
            t.configure(bg=c["field"], fg=c["fg"], insertbackground=c["fg"], selectbackground=c["select"],
                        selectforeground=c["select_fg"], highlightbackground=c["border"],
                        highlightcolor=c["accent"])
        self.dst.tag_configure("changed", background=c["changed"], underline=True)

    def _ctrl_enter(self, _e=None):
        self.correct()
        return "break"

    def _text_panel(self, panes, title):
        f = ttk.Frame(panes, padding=(0, 0, 6, 0))
        ttk.Label(f, text=title, font=self.app.font_bold).pack(anchor="w", pady=(0, 4))
        inner = ttk.Frame(f)
        inner.pack(fill="both", expand=True)
        t = tk.Text(inner, wrap="word", undo=True, font=self.app.font_text, padx=8, pady=6,
                    relief="flat", borderwidth=0, highlightthickness=1, width=40, height=10)
        sb = ttk.Scrollbar(inner, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        add_text_bindings(t)
        panes.add(f, weight=1)
        return t

    def on_provider(self, _e=None):
        p = self.provider.get()
        self.db.set_setting("corrector_provider", p)
        if p == PROVIDER_AI:
            self.settings_btn.state(["!disabled"])
            model = self.db.get_setting("ai_model", "")
            self.engine_info.configure(text="model: %s" % (model or "not set - open AI settings"))
        else:
            self.settings_btn.state(["disabled"])
            self.engine_info.configure(text="spelling, grammar, punctuation - free public service")

    def open_settings(self):
        if AISettingsDialog(self, self.db).show():
            self.on_provider()

    def paste(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return
        self.src.delete("1.0", "end")
        self.src.insert("1.0", text)

    def clear(self):
        self.src.delete("1.0", "end")
        self.set_result("", [], [])
        self.src.focus_set()

    def copy_result(self):
        text = self.dst.get("1.0", "end-1c")
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.status.configure(text="Corrected text copied to clipboard.")

    def use_result(self):
        text = self.dst.get("1.0", "end-1c")
        if text:
            self.src.delete("1.0", "end")
            self.src.insert("1.0", text)

    def correct(self):
        if self.busy:
            return
        text = self.src.get("1.0", "end-1c")
        if not text.strip():
            self.status.configure(text="Paste some Polish text first.")
            return
        if self.provider.get() == PROVIDER_AI:
            base = self.db.get_setting("ai_base_url", "")
            model = self.db.get_setting("ai_model", "")
            key = self.db.get_setting("ai_api_key", "")
            local = "localhost" in base or "127.0.0.1" in base
            if not base or not model or (not key and not local):
                messagebox.showinfo("AI settings", "Set up the AI provider and API key first.", parent=self)
                self.open_settings()
                return

            def job():
                return correct_ai(text, base, model, key)
        else:
            if len(text) > 18000:
                messagebox.showwarning("Text too long", "LanguageTool (free) accepts up to ~18 000 characters "
                                       "at once. Please split the text.", parent=self)
                return

            def job():
                return correct_languagetool(text)

        self.busy = True
        self.go_btn.state(["disabled"])
        self.progress.pack(side="left")
        self.progress.start(12)
        self.status.configure(text="Checking...")

        def worker():
            try:
                self.results.put(("ok", text, job()))
            except Exception as ex:  # show any error to the user
                self.results.put(("err", text, str(ex)))

        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, original, payload = self.results.get_nowait()
                self.busy = False
                self.progress.stop()
                self.progress.pack_forget()
                self.go_btn.state(["!disabled"])
                if kind == "err":
                    self.status.configure(text="Error: " + (payload.splitlines() or [""])[0][:200])
                    messagebox.showerror("Correction failed", payload, parent=self)
                    continue
                corrected, lt_changes = payload
                ranges, pairs = diff_changes(original, corrected)
                rows = lt_changes if lt_changes is not None else [(o, n, "") for o, n in pairs]
                self.set_result(corrected, ranges, rows)
                self.status.configure(text=("Done - %d change(s)." % len(rows)) if rows
                                      else "Done - no mistakes found.")
        except queue.Empty:
            pass
        try:
            self.after(100, self.poll)
        except tk.TclError:
            pass

    def set_result(self, text, ranges, rows):
        self.dst.delete("1.0", "end")
        self.dst.insert("1.0", text)
        for a, b in ranges:
            self.dst.tag_add("changed", "1.0 + %d chars" % a, "1.0 + %d chars" % b)
        self.changes.delete(*self.changes.get_children())
        for o, n, why in rows:
            self.changes.insert("", "end", values=(o, n, why))


# ----------------------------------------------------------------------------- main window

class CheckboxOverlay:
    """Draws big clickable checkboxes over one Treeview column (ttk cells can't hold widgets)."""

    def __init__(self, app, tree, column):
        self.app, self.tree, self.column = app, tree, column
        self.pool = []
        self.info = {}          # iid -> (row background, checked)
        self._job = None
        for seq in ("<Configure>", "<<TreeviewSelect>>", "<ButtonRelease-1>", "<B1-Motion>"):
            tree.bind(seq, lambda e: self.schedule(), add="+")

    def schedule(self):
        if self._job is None:
            self._job = self.tree.after_idle(self.draw)

    def _canvas(self, i):
        while len(self.pool) <= i:
            cv = tk.Canvas(self.tree, highlightthickness=0, bd=0, cursor="hand2")
            cv.iid = None
            cv.bind("<Button-1>", lambda e, cv=cv: self.app.toggle_row(cv.iid))
            cv.bind("<Double-1>", lambda e: "break")
            cv.bind("<Button-3>", lambda e, cv=cv: self._right_click(e, cv))
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                cv.bind(seq, self._wheel)
            self.pool.append(cv)
        return self.pool[i]

    def _wheel(self, e):
        if e.num == 4:
            step = -3
        elif e.num == 5:
            step = 3
        else:
            step = -3 if e.delta > 0 else 3
        self.tree.yview_scroll(step, "units")
        return "break"

    def _right_click(self, e, cv):
        self.tree.event_generate("<Button-3>", x=cv.winfo_x() + e.x, y=cv.winfo_y() + e.y,
                                 rootx=e.x_root, rooty=e.y_root)

    def draw(self):
        self._job = None
        c = self.app.c
        sel = set(self.tree.selection())
        height = self.tree.winfo_height()
        seen, order = set(), []
        for y in range(0, height, 6):
            iid = self.tree.identify_row(y)
            if iid and iid not in seen:
                seen.add(iid)
                order.append(iid)
        n = 0
        for iid in order:
            bb = self.tree.bbox(iid, self.column)
            if not bb or iid not in self.info:
                continue
            x, y, w, h = bb
            row_bg, checked = self.info[iid]
            bg = c["select"] if iid in sel else row_bg
            cv = self._canvas(n)
            n += 1
            cv.iid = iid
            cv.configure(bg=bg)
            cv.place(x=x, y=y, width=w, height=h)
            cv.delete("all")
            s = max(12, min(h - 8, px(22)))
            x0, y0 = (w - s) // 2, (h - s) // 2
            if checked:
                cv.create_rectangle(x0, y0, x0 + s, y0 + s, fill=c["accent"], outline=c["accent"], width=2)
                cv.create_line(x0 + s * 0.22, y0 + s * 0.52, x0 + s * 0.42, y0 + s * 0.74,
                               x0 + s * 0.80, y0 + s * 0.28, fill="#ffffff", width=3,
                               capstyle="round", joinstyle="round")
            else:
                cv.create_rectangle(x0, y0, x0 + s, y0 + s, fill=c["field"], outline=text_color(bg), width=2)
        for cv in self.pool[n:]:
            cv.place_forget()
            cv.iid = None


COLUMNS = {
    # id: (title, width, anchor, stretch)
    "n": ("#", 44, "center", False),
    "date": ("Date", 100, "center", False),
    "time": ("Time", 60, "center", False),
    "serial": ("Device number", 130, "w", True),
    "status": ("Status", 120, "w", True),
    "note": ("Note", 110, "w", True),
    "done": ("Handed", 84, "center", False),
}
DAY_COLUMNS = ["n", "time", "serial", "status", "note", "done"]
ALL_COLUMNS = ["n", "date", "time", "serial", "status", "note", "done"]

# Physical key codes (X11, Windows) - Ctrl shortcuts must work with Ukrainian/Russian layouts too.
KEYCODES = {"f": (41, 70), "c": (54, 67), "v": (55, 86), "x": (53, 88), "a": (38, 65), "z": (52, 90)}


class App(tk.Tk):
    def __init__(self, db, db_path):
        super().__init__()
        self.db = db
        self.db_path = db_path
        self.current_day = None          # None = "All days"
        self.statuses = []
        self.visible_rows = []
        self.sort_col = None
        self.sort_rev = False
        self.corrector = None

        self.title(APP_TITLE)
        global UI_SCALE
        UI_SCALE = max(1.0, self.winfo_fpixels("1i") / 96.0)
        self.report_callback_exception = self.on_error
        fit_window(self, 1320, 760, 980, 540)
        self.setup_style()
        self.build_ui()
        self.bind_keys()
        self.apply_theme_to_widgets()

        self.current_day = self.db.day_by_date(dt.date.today())
        self.refresh_all()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------------------------------------------------------- ui
    def setup_style(self):
        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(size=11)
            except tk.TclError:
                pass
        self.font_default = tkfont.nametofont("TkDefaultFont")
        family = self.font_default.actual("family")
        self.font_bold = tkfont.Font(self, family=family, size=11, weight="bold")
        self.font_big = tkfont.Font(self, family=family, size=15, weight="bold")
        self.font_text = tkfont.Font(self, family=family, size=12)
        theme = self.db.get_setting("theme", "dark")
        self.theme = theme if theme in THEMES else "dark"
        self.c = THEMES[self.theme]
        self.style_widgets()

    def row_color(self, color):
        """Status color as shown in the table for the current theme."""
        return mix_colors(color, self.c["field"], self.c["row_mix"])

    def style_widgets(self):
        c, st = self.c, self.style
        st.configure(".", background=c["bg"], foreground=c["fg"], fieldbackground=c["field"],
                     bordercolor=c["border"], darkcolor=c["bg"], lightcolor=c["bg"], troughcolor=c["panel"],
                     insertcolor=c["fg"], selectbackground=c["select"], selectforeground=c["select_fg"],
                     focuscolor=c["accent"], arrowcolor=c["fg"])
        st.map(".", background=[("disabled", c["bg"])], foreground=[("disabled", c["muted"])])
        flat = dict(bordercolor=c["border"], lightcolor=c["button"], darkcolor=c["button"])
        st.configure("TButton", background=c["button"], foreground=c["fg"], padding=(10, 4), **flat)
        st.map("TButton", background=[("disabled", c["bg"]), ("pressed", c["button_active"]),
                                      ("active", c["button_active"])],
               foreground=[("disabled", c["muted"])])
        st.configure("Accent.TButton", font=self.font_bold, background=c["accent"], foreground=c["accent_fg"],
                     bordercolor=c["accent"], lightcolor=c["accent"], darkcolor=c["accent"])
        st.map("Accent.TButton", background=[("disabled", c["panel"]), ("pressed", c["accent_active"]),
                                             ("active", c["accent_active"])],
               foreground=[("disabled", c["muted"])])
        st.configure("TMenubutton", background=c["button"], foreground=c["fg"], padding=(10, 4), **flat)
        st.map("TMenubutton", background=[("active", c["button_active"])])
        field = dict(fieldbackground=c["field"], foreground=c["fg"], bordercolor=c["border"],
                     lightcolor=c["field"], darkcolor=c["field"], insertcolor=c["fg"])
        st.configure("TEntry", padding=4, **field)
        st.map("TEntry", bordercolor=[("focus", c["accent"])], lightcolor=[("focus", c["accent"])])
        st.configure("TCombobox", background=c["button"], arrowcolor=c["fg"], padding=3, **field)
        st.map("TCombobox", fieldbackground=[("readonly", c["field"])], foreground=[("readonly", c["fg"])],
               selectbackground=[("readonly", c["field"])], selectforeground=[("readonly", c["fg"])],
               background=[("active", c["button_active"])], bordercolor=[("focus", c["accent"])])
        st.configure("Treeview", background=c["field"], fieldbackground=c["field"], foreground=c["fg"],
                     bordercolor=c["border"], lightcolor=c["field"], darkcolor=c["field"], rowheight=px(32))
        st.map("Treeview", background=[("selected", c["select"])], foreground=[("selected", c["select_fg"])])
        st.configure("Treeview.Heading", background=c["heading"], foreground=c["fg"], font=self.font_bold,
                     bordercolor=c["border"], lightcolor=c["heading"], darkcolor=c["heading"], padding=4)
        st.map("Treeview.Heading", background=[("active", c["button_active"])])
        for sb in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
            st.configure(sb, background=c["button"], troughcolor=c["bg"], bordercolor=c["bg"],
                         arrowcolor=c["fg"], lightcolor=c["button"], darkcolor=c["button"])
            st.map(sb, background=[("active", c["button_active"])])
        st.configure("TCheckbutton", background=c["bg"], foreground=c["fg"], indicatorbackground=c["field"],
                     indicatorforeground=c["fg"])
        st.map("TCheckbutton", background=[("active", c["bg"])],
               indicatorbackground=[("selected", c["accent"]), ("active", c["field"])],
               indicatorforeground=[("selected", c["accent_fg"])])
        st.configure("TLabelframe", background=c["bg"], bordercolor=c["border"],
                     lightcolor=c["bg"], darkcolor=c["bg"])
        st.configure("TLabelframe.Label", background=c["bg"], foreground=c["fg"])
        st.configure("TPanedwindow", background=c["bg"])
        st.configure("Sash", sashthickness=6, gripcount=0, background=c["bg"], lightcolor=c["bg"],
                     bordercolor=c["bg"])
        st.configure("TProgressbar", background=c["accent"], troughcolor=c["panel"], bordercolor=c["border"],
                     lightcolor=c["accent"], darkcolor=c["accent"])
        st.configure("TSeparator", background=c["border"])
        st.configure("Muted.TLabel", foreground=c["muted"])
        st.configure("Warn.TLabel", foreground=c["warn"])
        st.configure("Bold.TLabel", font=self.font_bold)
        st.configure("Status.TLabel", background=c["panel"], foreground=c["muted"])
        self.configure(bg=c["bg"])
        # classic Tk widgets: menus, simple dialogs, combobox drop-down lists
        for key, val in (("*Background", c["bg"]), ("*Foreground", c["fg"]),
                         ("*activeBackground", c["button_active"]), ("*activeForeground", c["fg"]),
                         ("*selectBackground", c["select"]), ("*selectForeground", c["select_fg"]),
                         ("*insertBackground", c["fg"]), ("*highlightBackground", c["bg"]),
                         ("*Entry.background", c["field"]), ("*Text.background", c["field"]),
                         ("*Listbox.background", c["field"]), ("*Menu.background", c["panel"]),
                         ("*Menu.activeBackground", c["select"]), ("*Menu.activeForeground", c["select_fg"]),
                         ("*Button.background", c["button"]), ("*disabledForeground", c["muted"])):
            self.option_add(key, val)

    def style_combobox_list(self, cb):
        """Recolor an already created combobox drop-down list."""
        try:
            pop = self.tk.call("ttk::combobox::PopdownWindow", str(cb))
            self.tk.call(pop + ".f.l", "configure", "-background", self.c["field"], "-foreground", self.c["fg"],
                         "-selectbackground", self.c["select"], "-selectforeground", self.c["select_fg"])
        except tk.TclError:
            pass

    def toggle_theme(self):
        self.theme = "light" if self.theme == "dark" else "dark"
        self.db.set_setting("theme", self.theme)
        self.c = THEMES[self.theme]
        self.style_widgets()
        self.apply_theme_to_widgets()

    def apply_theme_to_widgets(self):
        c = self.c
        set_dark_title_bar(self, self.theme == "dark")
        self.theme_btn.configure(text="☀ Light" if self.theme == "dark" else "☾ Dark")
        self.days_tree.tag_configure("all", font=self.font_bold, background=c["all_bg"], foreground=c["fg"])
        self.days_tree.tag_configure("match", background=c["match_bg"], foreground=c["match_fg"])
        self.tree.tag_configure("nostatus", background=c["field"], foreground=c["fg"])
        self.more_menu.configure(bg=c["panel"], fg=c["fg"], activebackground=c["select"],
                                 activeforeground=c["select_fg"])
        self.style_combobox_list(self.add_status_cb)
        self.reload_statuses()
        self.refresh_days()
        self.refresh_table()
        self.refresh_stats()
        if self.corrector is not None and self.corrector.winfo_exists():
            self.corrector.apply_theme()

    def build_ui(self):
        # --- toolbar
        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill="x")
        ttk.Label(top, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(top, textvariable=self.search_var, width=22)
        self.search_entry.pack(side="left", padx=(6, 2))
        ttk.Button(top, text="✕", width=3, command=lambda: self.search_var.set("")).pack(side="left")
        self.search_scope = ttk.Label(top, text="", style="Muted.TLabel")
        self.search_scope.pack(side="left", padx=8)
        self.search_var.trace_add("write", lambda *a: self.on_search())

        self.theme_btn = ttk.Button(top, text="", command=self.toggle_theme)
        self.theme_btn.pack(side="right", padx=(12, 2))
        self.more_menu = tk.Menu(self, tearoff=0)
        self.more_menu.add_command(label="Export CSV...", command=self.export_csv)
        self.more_menu.add_command(label="Open data folder", command=self.open_data_folder)
        self.more_menu.add_separator()
        self.more_menu.add_command(label="Add demo data (to try the app)", command=self.add_demo_data)
        self.more_menu.add_command(label="Remove demo data", command=self.remove_demo_data)
        ttk.Menubutton(top, text="More", menu=self.more_menu).pack(side="right", padx=2)
        ttk.Button(top, text="Polish Corrector", command=self.open_corrector).pack(side="right", padx=2)
        ttk.Button(top, text="Statuses...", command=self.open_statuses).pack(side="right", padx=2)
        ttk.Button(top, text="+ New day...", command=self.new_day).pack(side="right", padx=2)
        ttk.Button(top, text="Today", style="Accent.TButton", command=self.open_today).pack(side="right", padx=2)

        # --- status bar (packed before panes so it stays visible)
        self.status_bar = ttk.Label(self, text="", style="Status.TLabel", anchor="w", padding=(8, 4))
        self.status_bar.pack(fill="x", side="bottom")
        self.default_status = "Database: %s" % self.db_path
        self.status_bar.configure(text=self.default_status)

        # --- panes
        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=10)

        # days list
        left = ttk.Frame(panes)
        ttk.Label(left, text="Days", font=self.font_bold).pack(anchor="w", pady=(0, 4))
        self.days_tree = ttk.Treeview(left, columns=("date", "total", "done"), show="headings",
                                      selectmode="browse")
        for c, t, w, a in (("date", "Date", 130, "w"), ("total", "Total", 60, "center"),
                           ("done", "Handed", 72, "center")):
            self.days_tree.heading(c, text=t)
            self.days_tree.column(c, width=px(w), anchor=a, stretch=(c == "date"))
        self.days_tree.tag_configure("today", font=self.font_bold)
        dsb = ttk.Scrollbar(left, orient="vertical", command=self.days_tree.yview)
        self.days_tree.configure(yscrollcommand=dsb.set)
        self.days_tree.pack(side="left", fill="both", expand=True)
        dsb.pack(side="left", fill="y")
        self.days_tree.bind("<<TreeviewSelect>>", self.on_day_select)
        self.days_tree.bind("<Button-3>", self.on_day_right_click)
        if sys.platform == "darwin":
            self.days_tree.bind("<Button-2>", self.on_day_right_click)
        panes.add(left, weight=0)

        # center
        center = ttk.Frame(panes, padding=(10, 0))
        self.header = ttk.Label(center, text="", font=self.font_big)
        self.header.pack(anchor="w", pady=(0, 6))

        self.add_frame = ttk.Frame(center)
        self.add_frame.pack(fill="x", pady=(0, 6))
        ttk.Label(self.add_frame, text="Number:").pack(side="left")
        self.serial_var = tk.StringVar()
        self.serial_entry = ttk.Entry(self.add_frame, textvariable=self.serial_var, width=22)
        self.serial_entry.pack(side="left", padx=(4, 10))
        ttk.Label(self.add_frame, text="Status:").pack(side="left")
        self.add_status_var = tk.StringVar()
        self.add_status_cb = ttk.Combobox(self.add_frame, textvariable=self.add_status_var,
                                          state="readonly", width=22)
        self.add_status_cb.pack(side="left", padx=(4, 10))
        ttk.Label(self.add_frame, text="Note:").pack(side="left")
        self.note_var = tk.StringVar()
        self.note_entry = ttk.Entry(self.add_frame, textvariable=self.note_var, width=22)
        self.note_entry.pack(side="left", padx=(4, 10), fill="x", expand=True)
        self.add_btn = ttk.Button(self.add_frame, text="Add (Enter)", style="Accent.TButton",
                                  command=self.add_device)
        self.add_btn.pack(side="left")
        self.serial_entry.bind("<Return>", self.add_device)
        self.note_entry.bind("<Return>", self.add_device)
        self.add_hint = ttk.Label(center, style="Warn.TLabel",
                                  text="To add devices, select a day on the left or press \"Today\".")

        table = ttk.Frame(center)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=list(COLUMNS), show="headings", selectmode="extended")
        for cid, (title, width, anchor, stretch) in COLUMNS.items():
            self.tree.heading(cid, text=title, command=lambda c=cid: self.sort_by(c))
            self.tree.column(cid, width=px(width), anchor=anchor, stretch=stretch, minwidth=px(40))
        tsb = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.bind("<Configure>", lambda e: self.fit_columns(), add="+")
        self.checks = CheckboxOverlay(self, self.tree, "done")
        self.tree.configure(yscrollcommand=lambda a, b: (tsb.set(a, b), self.checks.schedule()))
        tsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.bind("<Double-1>", self.on_tree_double)
        self.tree.bind("<Button-3>", self.on_tree_right_click)
        if sys.platform == "darwin":
            self.tree.bind("<Button-2>", self.on_tree_right_click)
        self.tree.bind("<space>", lambda e: self.toggle_selected())
        self.tree.bind("<Delete>", lambda e: self.delete_selected())
        self.tree.bind("<Return>", lambda e: self.edit_selected())
        for k in range(1, 10):
            self.tree.bind(str(k), lambda e, k=k: self.set_status_by_index(k - 1))
            self.tree.bind("<KP_%d>" % k, lambda e, k=k: self.set_status_by_index(k - 1))

        ttk.Label(center, style="Muted.TLabel",
                  text="Click the checkbox - handed over · Double-click / Enter - edit · Right-click - menu\n"
                       "Space - mark selected · 1-9 - set status · Delete - delete · Ctrl+F - search by number, Enter - go to result"
                  ).pack(anchor="w", pady=4)
        panes.add(center, weight=1)

        # stats
        right = ttk.Frame(panes, padding=(6, 0))
        self.stats_title = ttk.Label(right, text="", font=self.font_bold, wraplength=px(250))
        self.stats_title.pack(anchor="w", pady=(0, 6))
        self.stats_box = ttk.Frame(right)
        self.stats_box.pack(fill="x")
        panes.add(right, weight=0)

    def bind_keys(self):
        self.bind("<Control-f>", lambda e: self.focus_search())
        self.bind("<Control-F>", lambda e: self.focus_search())
        self.bind("<Escape>", lambda e: self.search_var.set(""))
        self.bind_all("<Control-KeyPress>", self.on_ctrl_key, add="+")
        self.search_entry.bind("<Return>", self.search_jump)
        self.search_entry.bind("<Down>", self.search_jump)

    def on_ctrl_key(self, e):
        """Ctrl+F/C/V/X/A/Z by physical key, so they also work with a non-Latin keyboard layout."""
        if len(e.keysym) == 1 and e.keysym.isascii():
            return None                      # Latin layout: normal bindings already handle it
        if e.char and e.char.isprintable():
            return None                      # AltGr (= Ctrl+Alt on Windows) typing a letter like ą, ć, ż
        col = 1 if sys.platform.startswith("win") else 0
        key = next((k for k, codes in KEYCODES.items() if codes[col] == e.keycode), None)
        if key is None:
            return None
        w = e.widget
        if key == "f" and str(w.winfo_toplevel()) == str(self):
            self.focus_search()
            return "break"
        if isinstance(w, (tk.Entry, ttk.Entry, tk.Text)):
            ev = {"c": "<<Copy>>", "v": "<<Paste>>", "x": "<<Cut>>", "z": "<<Undo>>"}.get(key)
            if ev:
                w.event_generate(ev)
                return "break"
            if key == "a":
                if isinstance(w, tk.Text):
                    w.tag_add("sel", "1.0", "end-1c")
                else:
                    w.select_range(0, "end")
                    w.icursor("end")
                return "break"
        return None

    def search_jump(self, _e=None):
        """Enter / Down in the search box jumps to the first found row."""
        kids = self.tree.get_children()
        if kids:
            self.tree.selection_set(kids[0])
            self.tree.focus(kids[0])
            self.tree.see(kids[0])
            self.tree.focus_set()
        return "break"

    # ---------------------------------------------------------------- statuses
    def reload_statuses(self):
        self.statuses = list(self.db.statuses())
        for s in self.statuses:
            bg = self.row_color(s["color"])
            self.tree.tag_configure("s%d" % s["id"], background=bg, foreground=text_color(bg))
        names = ["%d. %s" % (i + 1, s["name"]) if i < 9 else s["name"] for i, s in enumerate(self.statuses)]
        self.add_status_cb.configure(values=names)
        if self.add_status_var.get() not in names:
            self.add_status_var.set(names[0] if names else "")

    def status_id_by_name(self, name):
        name = (name or "").strip()
        for i, s in enumerate(self.statuses):
            if name in (s["name"], "%d. %s" % (i + 1, s["name"])):
                return s["id"]
        return None

    def on_statuses_changed(self):
        self.reload_statuses()
        self.refresh_table()
        self.refresh_stats()

    def open_statuses(self):
        StatusesDialog(self).show()

    def open_corrector(self):
        if self.corrector is not None and self.corrector.winfo_exists():
            self.corrector.deiconify()
            self.corrector.lift()
            self.corrector.focus_force()
            return
        self.corrector = CorrectorWindow(self)

    # ---------------------------------------------------------------- refresh
    def refresh_all(self, select=None):
        self.refresh_days()
        self.refresh_header()
        self.refresh_table(select)
        self.refresh_stats()

    def refresh_header(self):
        if self.current_day is None:
            self.header.configure(text="All days")
            self.add_frame.pack_forget()
            self.add_hint.pack(anchor="w", pady=(0, 6), before=self.tree.master)
            self.tree.configure(displaycolumns=ALL_COLUMNS)
            self.fit_columns()
            self.search_scope.configure(text="in all days")
        else:
            iso = self.db.day_date(self.current_day)
            d = dt.date.fromisoformat(iso)
            extra = "  - today" if d == dt.date.today() else ""
            self.header.configure(text="Day: %s (%s)%s" % (fmt_date(iso), WEEKDAYS[d.weekday()], extra))
            self.add_hint.pack_forget()
            self.add_frame.pack(fill="x", pady=(0, 6), before=self.tree.master)
            self.tree.configure(displaycolumns=DAY_COLUMNS)
            self.fit_columns()
            self.search_scope.configure(text="in this day")

    def fit_columns(self):
        """Share the table width between columns so the checkbox column on the right is always visible."""
        width = self.tree.winfo_width() - 4
        if width < 100:
            return
        shown = list(self.tree.cget("displaycolumns"))
        if shown == ["#all"] or not shown:
            shown = list(COLUMNS)
        fixed = sum(px(COLUMNS[c][1]) for c in shown if not COLUMNS[c][3])
        flex = [c for c in shown if COLUMNS[c][3]]
        base = sum(COLUMNS[c][1] for c in flex) or 1
        free = max(width - fixed, len(flex) * px(60))
        for c in shown:
            w = px(COLUMNS[c][1]) if not COLUMNS[c][3] else int(free * COLUMNS[c][1] / base)
            self.tree.column(c, width=w)
        self.checks.schedule()

    def refresh_days(self):
        search = self.search_var.get().strip()
        self.days_tree.delete(*self.days_tree.get_children())
        rows = self.db.days(search)
        all_found = "  [%d]" % int(sum(r["found"] for r in rows)) if search else ""
        total = sum(r["total"] for r in rows)
        done = int(sum(r["done"] for r in rows))
        self.days_tree.insert("", "end", iid="all", tags=("all",),
                              values=("All days" + all_found, total, done))
        today = dt.date.today().isoformat()
        for r in rows:
            label = fmt_date(r["date"])
            tags = []
            if r["date"] == today:
                tags.append("today")
            if search and r["found"]:
                label += "  [%d]" % int(r["found"])
                tags.append("match")
            self.days_tree.insert("", "end", iid="d%d" % r["id"], tags=tags,
                                  values=(label, r["total"], int(r["done"])))
        iid = "all" if self.current_day is None else "d%d" % self.current_day
        if not self.days_tree.exists(iid):
            self.current_day = None
            iid = "all"
        self.days_tree.selection_set(iid)
        self.days_tree.see(iid)

    def sorted_rows(self, rows):
        if not self.sort_col:
            return rows
        keys = {
            "done": lambda r: r["done"],
            "n": lambda r: r["id"],
            "date": lambda r: (r["date"], r["id"]),
            "serial": lambda r: r["serial"].lower(),
            "status": lambda r: next((i for i, s in enumerate(self.statuses) if s["id"] == r["status_id"]), 999),
            "note": lambda r: r["note"].lower(),
            "time": lambda r: r["created"],
        }
        return sorted(rows, key=keys[self.sort_col], reverse=self.sort_rev)

    def refresh_table(self, select=None):
        keep = set(self.tree.selection())
        if select is not None:
            keep = {str(select)}
        self.tree.delete(*self.tree.get_children())
        rows = self.db.devices(self.current_day, self.search_var.get().strip())
        if self.current_day is not None and not self.sort_col:
            rows = sorted(rows, key=lambda r: r["id"])
        rows = self.sorted_rows(rows)
        self.visible_rows = rows
        self.checks.info = {}
        for i, r in enumerate(rows, 1):
            has_status = r["status_id"] is not None and r["sname"]
            tag = "s%d" % r["status_id"] if has_status else "nostatus"
            time = r["created"][11:16] if r["created"] else ""
            self.tree.insert("", "end", iid=str(r["id"]), tags=(tag,), values=(
                i, fmt_date(r["date"]), time, r["serial"], r["sname"] or "-", r["note"], ""))
            self.checks.info[str(r["id"])] = (self.row_color(r["color"]) if has_status else self.c["field"],
                                              bool(r["done"]))
        self.checks.schedule()
        sel = [i for i in keep if self.tree.exists(i)]
        if sel:
            self.tree.selection_set(sel)
            self.tree.see(sel[-1] if select is None else str(select))
        for cid, (title, *_rest) in COLUMNS.items():
            arrow = (" ▼" if self.sort_rev else " ▲") if cid == self.sort_col else ""
            self.tree.heading(cid, text=title + arrow)

    def refresh_stats(self):
        for w in self.stats_box.winfo_children():
            w.destroy()
        by, total, done = self.db.stats(self.current_day)
        if self.current_day is None:
            self.stats_title.configure(text="Statistics - all time")
        else:
            self.stats_title.configure(text="Statistics - %s" % fmt_date(self.db.day_date(self.current_day)))

        known = set()
        for s in self.statuses:
            known.add(s["id"])
            c, d = by.get(s["id"], (0, 0))
            self._stat_row(s["name"], c, d, s["color"])
        orphan = sum(c for sid, (c, _) in by.items() if sid not in known)
        orphan_done = sum(d for sid, (_, d) in by.items() if sid not in known)
        if orphan:
            self._stat_row("No status", orphan, orphan_done, "#ffffff")

        ttk.Separator(self.stats_box).pack(fill="x", pady=8)
        for label, val in (("Total:", total), ("Handed over ☑:", done), ("Not handed over ☐:", total - done)):
            r = ttk.Frame(self.stats_box)
            r.pack(fill="x", pady=1)
            ttk.Label(r, text=label, font=self.font_bold).pack(side="left")
            ttk.Label(r, text=str(val), font=self.font_bold).pack(side="right")

    def _stat_row(self, name, count, done, color):
        color = self.row_color(color)
        fg = text_color(color)
        r = tk.Frame(self.stats_box, bg=color, highlightthickness=1, highlightbackground=self.c["border"])
        r.pack(fill="x", pady=2)
        tk.Label(r, text=name, bg=color, fg=fg, anchor="w", padx=6, pady=3).pack(side="left", fill="x",
                                                                                 expand=True)
        tk.Label(r, text="handed %d" % done, bg=color, fg=fg, padx=4).pack(side="right")
        tk.Label(r, text=str(count), bg=color, fg=fg, font=self.font_bold, padx=6).pack(side="right")

    # ---------------------------------------------------------------- events
    def on_search(self):
        self.refresh_days()
        self.refresh_table()

    def focus_search(self):
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")

    def on_day_select(self, _e=None):
        sel = self.days_tree.selection()
        if not sel:
            return
        new = None if sel[0] == "all" else int(sel[0][1:])
        if new == self.current_day:
            return
        self.current_day = new
        self.sort_col = None
        self.sort_rev = False
        self.refresh_header()
        self.refresh_table()
        self.refresh_stats()
        if new is not None:
            self.serial_entry.focus_set()

    def go_to_day(self, day_id):
        self.current_day = day_id
        self.sort_col = None
        self.refresh_all()

    def on_day_right_click(self, e):
        iid = self.days_tree.identify_row(e.y)
        if not iid or iid == "all":
            return
        self.days_tree.selection_set(iid)
        day_id = int(iid[1:])
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="Delete day...", command=lambda: self.delete_day(day_id))
        m.tk_popup(e.x_root, e.y_root)

    def on_tree_click(self, e):
        if self.tree.identify_region(e.x, e.y) != "cell":
            return None
        try:
            cid = self.tree.column(self.tree.identify_column(e.x), "id")
        except tk.TclError:
            return None
        if cid != "done":
            return None
        row = self.tree.identify_row(e.y)
        if not row:
            return None
        return self.toggle_row(row)

    def toggle_row(self, iid):
        if iid and iid in self.checks.info:
            self.db.set_done([int(iid)], not self.checks.info[iid][1])
            self.after_change()
        return "break"

    def on_tree_double(self, e):
        if self.tree.identify_region(e.x, e.y) != "cell":
            return None
        if self.tree.column(self.tree.identify_column(e.x), "id") == "done":
            return "break"
        row = self.tree.identify_row(e.y)
        if row:
            self.tree.selection_set(row)
            self.edit_selected()
        return "break"

    def on_tree_right_click(self, e):
        row = self.tree.identify_row(e.y)
        if not row:
            return
        if row not in self.tree.selection():
            self.tree.selection_set(row)
        m = tk.Menu(self, tearoff=0)
        sub = tk.Menu(m, tearoff=0)
        for i, s in enumerate(self.statuses):
            sub.add_command(label=("%d. " % (i + 1) if i < 9 else "") + s["name"],
                            background=s["color"], foreground=text_color(s["color"]),
                            command=lambda sid=s["id"]: self.set_status_selected(sid))
        m.add_cascade(label="Change status", menu=sub)
        m.add_command(label="Toggle handed over (Space)", command=self.toggle_selected)
        m.add_command(label="Edit... (Enter)", command=self.edit_selected)
        if self.current_day is None:
            r = self.db.device(int(row))
            if r:
                m.add_command(label="Go to day %s" % fmt_date(r["date"]),
                              command=lambda d=r["day_id"]: self.go_to_day(d))
        m.add_separator()
        m.add_command(label="Delete (Delete)", command=self.delete_selected)
        m.tk_popup(e.x_root, e.y_root)

    def sort_by(self, col):
        if self.sort_col == col:
            if self.sort_rev:
                self.sort_col, self.sort_rev = None, False
            else:
                self.sort_rev = True
        else:
            self.sort_col, self.sort_rev = col, False
        self.refresh_table()

    # ---------------------------------------------------------------- actions
    def after_change(self, select=None):
        self.refresh_days()
        self.refresh_table(select)
        self.refresh_stats()

    def selected_ids(self):
        return [int(i) for i in self.tree.selection()]

    def add_device(self, _e=None):
        if self.current_day is None:
            return "break"
        serial = self.serial_var.get().strip()
        if not serial:
            self.serial_entry.focus_set()
            return "break"
        sid = self.status_id_by_name(self.add_status_var.get())
        found = self.db.find_serial(serial)
        same = [r for r in found if r["day_id"] == self.current_day]
        if same and not messagebox.askyesno(
                "Duplicate", "Number \"%s\" is already in this day.\nAdd it again?" % serial, parent=self):
            return "break"
        new_id = self.db.add_device(self.current_day, serial, sid, self.note_var.get().strip())
        other = [r for r in found if r["day_id"] != self.current_day]
        msg = "Added: %s" % serial
        if other:
            msg += "   |   This number was seen before: " + ", ".join(
                "%s (%s)" % (fmt_date(r["date"]), r["sname"] or "no status") for r in other[:5])
            if len(other) > 5:
                msg += " ... and %d more" % (len(other) - 5)
        self.flash(msg)
        self.serial_var.set("")
        self.note_var.set("")
        self.serial_entry.focus_set()
        self.after_change(select=new_id)
        return "break"

    def toggle_selected(self):
        ids = self.selected_ids()
        if not ids:
            return "break"
        all_done = all(self.checks.info.get(str(i), (None, False))[1] for i in ids)
        self.db.set_done(ids, not all_done)
        self.after_change()
        return "break"

    def set_status_selected(self, sid):
        ids = self.selected_ids()
        if ids:
            self.db.set_status(ids, sid)
            self.after_change()

    def set_status_by_index(self, idx):
        if 0 <= idx < len(self.statuses):
            self.set_status_selected(self.statuses[idx]["id"])
        return "break"

    def edit_selected(self):
        ids = self.selected_ids()
        if not ids:
            return "break"
        row = self.db.device(ids[0])
        if not row:
            return "break"
        res = DeviceDialog(self, row).show()
        if res:
            self.db.update_device(ids[0], *res)
            self.after_change(select=ids[0])
        return "break"

    def delete_selected(self):
        ids = self.selected_ids()
        if not ids:
            return "break"
        if len(ids) == 1:
            msg = "Delete record \"%s\"?" % self.tree.set(str(ids[0]), "serial")
        else:
            msg = "Delete %d records?" % len(ids)
        if messagebox.askyesno("Delete", msg, parent=self):
            self.db.delete_devices(ids)
            self.after_change()
        return "break"

    def open_today(self):
        self.go_to_day(self.db.get_or_create_day(dt.date.today()))
        self.serial_entry.focus_set()

    def new_day(self):
        text = simpledialog.askstring("New day", "Date (DD.MM.YYYY):",
                                      initialvalue=dt.date.today().strftime("%d.%m.%Y"), parent=self)
        if text is None:
            return
        d = parse_date(text)
        if not d:
            messagebox.showwarning("Error", "Could not read the date: %s" % text, parent=self)
            return
        self.go_to_day(self.db.get_or_create_day(d))
        self.serial_entry.focus_set()

    def delete_day(self, day_id):
        iso = self.db.day_date(day_id)
        n = len(self.db.devices(day_id))
        if not messagebox.askyesno(
                "Delete day",
                "Delete day %s with all its records (%d)?\nThis cannot be undone." % (fmt_date(iso), n),
                parent=self):
            return
        self.db.delete_day(day_id)
        if self.current_day == day_id:
            self.current_day = None
        self.refresh_all()

    def export_csv(self):
        if not self.visible_rows:
            messagebox.showinfo("Export", "There are no records to export.", parent=self)
            return
        name = "devices_%s.csv" % ("all_days" if self.current_day is None else self.db.day_date(self.current_day))
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", initialfile=name,
                                            filetypes=[("CSV (Excel)", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(["Date", "Number", "Status", "Handed over", "Note", "Time"])
                for r in self.visible_rows:
                    w.writerow([fmt_date(r["date"]), r["serial"], r["sname"] or "",
                                "yes" if r["done"] else "no", r["note"], (r["created"] or "")[11:16]])
        except OSError as ex:
            messagebox.showerror("Error", str(ex), parent=self)
            return
        self.flash("Exported %d records to %s" % (len(self.visible_rows), path))

    def open_data_folder(self):
        folder = os.path.dirname(self.db_path)
        try:
            if sys.platform.startswith("win"):
                os.startfile(folder)  # noqa - Windows only
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as ex:
            messagebox.showinfo("Data folder", "%s\n\n(%s)" % (folder, ex), parent=self)

    def add_demo_data(self):
        if not messagebox.askyesno(
                "Demo data", "Add sample records for the last 10 working days?\n\n"
                "They are marked as demo and can be removed any time with\nMore ▾ → Remove demo data.\n"
                "Your own records are never touched.", parent=self):
            return
        n = self.db.add_demo_data([s["id"] for s in self.statuses])
        self.current_day = self.db.day_by_date(dt.date.today())
        self.refresh_all()
        self.flash("Added %d demo records." % n)

    def remove_demo_data(self):
        n = self.db.demo_count()
        if not n:
            messagebox.showinfo("Demo data", "There is no demo data.", parent=self)
            return
        if messagebox.askyesno("Remove demo data", "Remove %d demo records?\nYour own records stay." % n,
                               parent=self):
            self.db.remove_demo_data()
            self.refresh_all()
            self.flash("Removed %d demo records." % n)

    def flash(self, msg):
        self.status_bar.configure(text=msg)
        if getattr(self, "_flash_job", None):
            self.after_cancel(self._flash_job)
        self._flash_job = self.after(12000, lambda: self.status_bar.configure(text=self.default_status))

    def on_error(self, exc, val, tb):
        import traceback
        text = "".join(traceback.format_exception(exc, val, tb))
        log_error(text)
        messagebox.showerror("Unexpected error",
                             "%s\n\nDetails were saved to:\n%s" % (val, os.path.join(data_dir(), "error.log")),
                             parent=self)

    def on_close(self):
        try:
            self.db.conn.close()
        finally:
            self.destroy()


def main():
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    try:
        path = os.path.join(data_dir(), "devices.db")
        make_backup(path)
        app = App(DB(path), path)
    except Exception:
        import traceback
        log_error(traceback.format_exc())
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(APP_TITLE, "The program could not start:\n\n%s\n\nSee error.log in:\n%s"
                                 % (sys.exc_info()[1], data_dir()))
        except Exception:
            pass
        raise
    app.mainloop()


if __name__ == "__main__":
    main()
