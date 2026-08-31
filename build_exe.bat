@echo off
REM ============================================================
REM  Build single exe (Windows). Output: dist\CGV-Seat-Watcher.exe
REM  Uses the installed Chrome at runtime (no Chromium bundled) => small exe.
REM  Requires Python 3.10+ (Add to PATH).
REM ============================================================
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Install from https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist ".venv\" python -m venv .venv
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install pyyaml playwright customtkinter pyinstaller

echo [build] running PyInstaller...
pyinstaller --noconfirm --onefile --windowed --name CGV-Seat-Watcher ^
  --collect-all playwright --collect-all customtkinter ^
  --hidden-import cgv_macro.watcher --hidden-import cgv_macro.replayer ^
  --hidden-import cgv_macro.recorder --hidden-import cgv_macro.cgv_api ^
  app.py

echo.
echo   Done: dist\CGV-Seat-Watcher.exe
echo   (Google Chrome must be installed on the PC.)
pause
