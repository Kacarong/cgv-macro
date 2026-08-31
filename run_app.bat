@echo off
REM ==== CGV seat watcher app (double-click) ====
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Install Python 3.10+ from https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist ".venv\" (
  echo [setup] first-time setup...
  python -m venv .venv
  call ".venv\Scripts\activate.bat"
  python -m pip install --upgrade pip
  pip install pyyaml playwright
) else (
  call ".venv\Scripts\activate.bat"
)

python app.py
