@echo off
cd /d "%~dp0"
set "LOG=%~dp0last_run.txt"
echo ==== run_autoclick start ==== > "%LOG%"

where python >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] Python not found. Install Python 3.10+ from https://www.python.org/downloads/
  echo [ERROR] Python not found >> "%LOG%"
  pause
  exit /b 1
)
python --version >> "%LOG%" 2>&1

if not exist ".venv\" (
  echo [setup] first-time setup, please wait a few minutes...
  echo [setup] creating venv >> "%LOG%"
  python -m venv .venv >> "%LOG%" 2>&1
  call ".venv\Scripts\activate.bat"
  python -m pip install --upgrade pip >> "%LOG%" 2>&1
  pip install pyyaml playwright >> "%LOG%" 2>&1
  python -m playwright install chromium >> "%LOG%" 2>&1
) else (
  call ".venv\Scripts\activate.bat"
)

echo [run] starting auto-click... progress is saved to last_run.txt
echo ==== auto_click output ==== >> "%LOG%"
python auto_click.py --count 2 --prefer center >> "%LOG%" 2>&1

echo.
echo ===== finished. If something went wrong, send last_run.txt =====
pause
