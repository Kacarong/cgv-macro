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
  python -m playwright install chromium
) else (
  call .venv\Scripts\activate.bat
)

if not exist "config.yaml" (
  echo [!] config.yaml not found. Copying example...
  copy config.example.yaml config.yaml
  echo [!] Edit config.yaml then run again. Or use the GUI: python cgv_gui.py
  pause
  exit /b 1
)

echo [run] Starting GUI...
python cgv_gui.py
pause
