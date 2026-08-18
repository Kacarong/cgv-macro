@echo off
REM ============================================================
REM  CGV 취소표 감시기 - Windows exe 빌드 스크립트 (더블클릭 1회 실행)
REM  결과물: dist\CGV취소표감시기\CGV취소표감시기.exe
REM  ※ Python 3.10+ 이 설치돼 있어야 합니다(설치 시 "Add to PATH" 체크).
REM ============================================================
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [!] Python 이 설치돼 있지 않습니다. https://www.python.org/downloads/ 에서 설치하세요.
  pause
  exit /b 1
)

echo [1/5] 가상환경 준비...
if not exist ".venv\" python -m venv .venv
call .venv\Scripts\activate.bat

echo [2/5] 의존성 설치...
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller

echo [3/5] Chromium 다운로드(앱에 번들됩니다, 수백 MB)...
set PLAYWRIGHT_BROWSERS_PATH=%CD%\ms-playwright
python -m playwright install chromium

echo [4/5] exe 빌드(PyInstaller)...
pyinstaller --noconfirm cgv_gui.spec

echo [5/5] 완료!
echo.
echo  실행 파일: dist\CGV취소표감시기\CGV취소표감시기.exe
echo  이 폴더(dist\CGV취소표감시기) 전체를 복사해서 사용하세요.
echo.
pause
