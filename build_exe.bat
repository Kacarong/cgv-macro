@echo off
REM ============================================================
REM  CGV Ticket Watcher - Windows exe build script
REM  Output: dist\CGV-Ticket-Watcher\CGV-Ticket-Watcher.exe
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

echo [1/5] Preparing virtual environment...
if not exist ".venv\" python -m venv .venv
call .venv\Scripts\activate.bat

echo [2/5] Installing dependencies...
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller

echo [3/5] Downloading Chromium ^(bundled into the app, a few hundred MB^)...
set "PLAYWRIGHT_BROWSERS_PATH=%CD%\ms-playwright"
python -m playwright install chromium

echo [4/5] Building exe with PyInstaller...
pyinstaller --noconfirm cgv_gui.spec

echo [5/5] Done.
echo.
echo   Executable: dist\CGV-Ticket-Watcher\CGV-Ticket-Watcher.exe
echo   Copy the whole folder dist\CGV-Ticket-Watcher to use it.
echo.
pause
