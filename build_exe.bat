@echo off
REM ============================================================
REM  CGV Ticket Watcher - Windows exe build (double-click once)
REM  Output: dist\CGV-Ticket-Watcher.exe  (single file, small)
REM  Requires Python 3.10+ (check "Add Python to PATH" on install)
REM ============================================================
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Install from https://www.python.org/downloads/
  pause
  exit /b 1
)

echo [1/3] Preparing virtual environment...
if not exist ".venv\" python -m venv .venv
call .venv\Scripts\activate.bat

echo [2/3] Installing dependencies...
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller

echo [3/3] Building exe...
pyinstaller --noconfirm --onefile --windowed --name CGV-Ticket-Watcher cgv_gui.py

echo.
echo   Done. Executable: dist\CGV-Ticket-Watcher.exe
echo.
pause
