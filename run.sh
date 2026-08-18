#!/usr/bin/env bash
# ===== CGV Ticket Watcher - run (macOS / Linux) =====
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "[setup] Creating virtual environment..."
  python3 -m venv .venv
  source .venv/bin/activate
  python -m pip install --upgrade pip
  pip install -r requirements.txt
else
  source .venv/bin/activate
fi

echo "[run] Starting GUI..."
exec python cgv_gui.py
