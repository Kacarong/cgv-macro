@echo off
REM ===== CGV 감시 실행 (Windows) =====
REM 최초 1회만: 가상환경 + 의존성 + 브라우저 설치
setlocal

if not exist ".venv\" (
  echo [setup] 가상환경 생성...
  python -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip
  pip install -r requirements.txt
  python -m playwright install chromium
) else (
  call .venv\Scripts\activate.bat
)

if not exist "config.yaml" (
  echo [!] config.yaml 이 없습니다. config.example.yaml 을 복사해 설정하세요.
  copy config.example.yaml config.yaml
  echo [!] config.yaml 을 편집한 뒤 다시 실행하세요.
  pause
  exit /b 1
)

REM 로그인 세션이 없으면 안내
python -m cgv_macro --check-login
if errorlevel 1 (
  echo [!] 먼저 로그인하세요:  python login_setup.py
  pause
)

echo [run] 감시 시작...
python -m cgv_macro --config config.yaml
pause
