"""
모든 감시(취소표·상영오픈·무대인사)가 공유하는 단일 브라우저 허브.

- 크롬(persistent profile)은 딱 1개만 띄운다 → 같은 프로필 중복 실행 잠금 오류 방지.
- 로그인은 1회만(프로필에 세션 저장 → 자리에 없어도 자동 로그인 상태).
- 좌석 잡기는 book_lock 으로 직렬화 → 여러 대상이 거의 동시에 감지돼도
  한 번에 하나만 예매하고, 하나 선점되면 held 로 전체 감시를 멈춘다.
"""
from __future__ import annotations

import logging
import threading

from .replayer import Grabber

logger = logging.getLogger("cgv_macro")


class WatchHub:
    def __init__(self) -> None:
        self._grabber: Grabber | None = None
        self._launch_lock = threading.Lock()
        self.book_lock = threading.Lock()   # 동시에 하나만 좌석잡기
        self.held = threading.Event()       # 좌석 선점 완료 → 모든 감시 중단 신호
        self.logged_in = False

    def grabber(self, log=None) -> Grabber:
        with self._launch_lock:
            if self._grabber is None:
                if log:
                    log("[브라우저] 크롬 실행 중…")
                self._grabber = Grabber(headless=False).__enter__()
            return self._grabber

    def ensure_login(self, log=None, timeout_s: int = 600) -> bool:
        g = self.grabber(log)
        with self._launch_lock:
            if self.logged_in:
                return True
        if log:
            log("[브라우저] 로그인 확인 중(이미 로그인돼 있으면 자동 통과)…")
        ok = g.ensure_login(timeout_s=timeout_s)
        if ok:
            with self._launch_lock:
                self.logged_in = True
            if log:
                log("[브라우저] 로그인 확인 완료 — 세션 유지됨")
        return ok

    def close(self) -> None:
        with self._launch_lock:
            if self._grabber:
                try:
                    self._grabber.close()
                except Exception:  # noqa: BLE001
                    pass
            self._grabber = None
            self.logged_in = False
        self.held.clear()
