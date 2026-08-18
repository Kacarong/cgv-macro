r"""
앱 데이터 경로 — 실행 위치(exe 폴더/작업폴더)와 무관한 '고정 폴더'.

exe 를 다시 빌드하거나 python 으로 실행하거나 상관없이 설정/상태/로그/로그인세션이
항상 같은 곳에 유지되도록 사용자 데이터 폴더에 저장한다.

  Windows: %APPDATA%\CGV-Ticket-Watcher\
  기타:    ~/.config/CGV-Ticket-Watcher/
"""
from __future__ import annotations

import os

APP_NAME = "CGV-Ticket-Watcher"


def data_dir() -> str:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config"
        )
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def config_path() -> str:
    return os.path.join(data_dir(), "config.yaml")


def state_path() -> str:
    return os.path.join(data_dir(), "state.json")


def logs_dir() -> str:
    d = os.path.join(data_dir(), "logs")
    os.makedirs(d, exist_ok=True)
    return d


def session_dir() -> str:
    return os.path.join(data_dir(), "session")
