#!/usr/bin/env bash
# ===== CGV 감시 실행 (macOS / Linux) =====
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "[setup] 가상환경 생성..."
  python3 -m venv .venv
  source .venv/bin/activate
  python -m pip install --upgrade pip
  pip install -r requirements.txt
  python -m playwright install chromium
else
  source .venv/bin/activate
fi

if [ ! -f "config.yaml" ]; then
  echo "[!] config.yaml 이 없습니다. 예시를 복사합니다."
  cp config.example.yaml config.yaml
  echo "[!] config.yaml 을 편집한 뒤 다시 실행하세요."
  exit 1
fi

# 로그인 세션 확인(없으면 안내만)
python -m cgv_macro --check-login || echo "[!] 먼저 로그인하세요:  python login_setup.py"

echo "[run] 감시 시작..."
exec python -m cgv_macro --config config.yaml
