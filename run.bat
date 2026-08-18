@echo off
REM ===== CGV Ticket Watcher - run via Python (Windows) =====
setlocal
cd /d "%~dp0"

if not exist ".venv\" (
  echo [setup] Creating virtual environment...
  python -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate.bat
)

echo [run] Starting GUI...
python cgv_gui.py
pause
