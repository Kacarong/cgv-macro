@echo off
REM ==== CGV auto-click launcher (double-click to run) ====
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Install Python 3.10+ from https://www.python.org/downloads/
  echo         (check "Add Python to PATH" during install)
  pause
  exit /b 1
)

if not exist ".venv\" (
  echo [setup] first-time setup, please wait a few minutes...
  python -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip
  pip install pyyaml playwright
  python -m playwright install chromium
) else (
  call .venv\Scripts\activate.bat
)

echo [run] starting auto-click (Ctrl+C to stop)...
python auto_click.py --count 2 --prefer center
pause
