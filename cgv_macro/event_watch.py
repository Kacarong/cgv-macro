"""
무대인사(GV·관객과의 대화·내한 등) 상시 감지 → 감지 즉시 좌석 자동 잡기.

영화를 지정하지 않고, 선택한 극장들에서 '상영중 전체 영화'를 매 사이클마다
새로 불러와(=최신화) 훑는다. 개봉 전 영화의 무대인사가 예매 오픈되는 순간
그 영화가 예매목록에 등장하므로 자동으로 감지 대상에 포함된다.

CGV 회차 API(searchSchByMov)는 극장(siteNo)을 반드시 요구하므로
'전 극장'이 아니라 사용자가 고른 극장 집합만 훑는다(차단/429 회피).
요청 간에는 딜레이+지터를 둔다.
"""
from __future__ import annotations

import logging
import random
import time
from contextlib import nullcontext
from datetime import date, timedelta

from . import cgv_api
from .replayer import Grabber

logger = logging.getLogger("cgv_macro")
_NULL_LOCK = nullcontext()


class EventWatcher:
    def __init__(self, recipe: dict, theaters: list[tuple[str, str]],
                 base: dict, notifier=None, days: int = 7, hub=None) -> None:
        # theaters: [(siteNo, siteNm), ...]
        self.recipe = recipe
        self.theaters = theaters
        self.base = base or {}
        self.notifier = notifier
        self.days = max(1, min(int(days), 21))
        self.hub = hub
        self.grabber: Grabber | None = None
        self.seen: set[str] = set()

    def _held(self) -> bool:
        return self.hub is not None and self.hub.held.is_set()

    def run(self, stop_event, log=None) -> None:
        log = log or logger.info
        b = self.base
        persons = {k: int(v) for k, v in (b.get("persons") or {"일반": 2}).items() if int(v) > 0}
        need = sum(persons.values()) or 1
        preferred = b.get("preferred") or []
        only = bool(b.get("only_preferred"))
        prefer = b.get("prefer", "center")
        interval = max(10, int(b.get("interval", 30)))
        dates = [(date.today() + timedelta(days=i)).strftime("%Y%m%d") for i in range(self.days)]

        if not self.theaters:
            log("[무대인사] 감지할 극장을 1곳 이상 선택하세요.")
            return
        th_names = ", ".join(nm for _no, nm in self.theaters)
        log(f"[무대인사] 감지 시작 — 극장 {len(self.theaters)}곳({th_names}) × 향후 {self.days}일 "
            f"× 상영중 전체영화 (주기 {interval}s, 좌석 {need}석)")

        if self.hub is not None:
            self.grabber = self.hub.grabber(log)
            if not self.hub.ensure_login(log):
                log("[무대인사] 로그인 실패 — 중지")
                return
        else:
            self.grabber = Grabber(headless=False).__enter__()
            log("[무대인사] 크롬에 로그인하세요(이미 되어있으면 자동 통과)...")
            if not self.grabber.ensure_login(timeout_s=600):
                log("[무대인사] 로그인 실패 — 중지")
                return
        log("[무대인사] 로그인 확인 → 감시 시작")

        while not stop_event.is_set() and not self._held():
            try:
                movies = cgv_api.list_movies()  # 매 사이클 최신화(개봉 전 신규 자동 포착)
            except Exception as e:  # noqa: BLE001
                log(f"[무대인사] 영화목록 조회 오류: {e}")
                self._sleep(interval, stop_event)
                continue
            log(f"[무대인사] 스윕 시작 — 상영중 {len(movies)}편 확인")
            if self._sweep(movies, dates, need, persons, preferred, only, prefer, stop_event, log):
                return  # 좌석 선점 완료 → 종료
            if not stop_event.is_set():
                log(f"[무대인사] 스윕 완료 — 신규 무대인사 없음. {interval}s 후 재스윕")
            self._sleep(interval, stop_event)
        log("[무대인사] 종료")

    def _sweep(self, movies, dates, need, persons, preferred, only, prefer, stop_event, log) -> bool:
        for ymd in dates:
            disp = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
            for site_no, site_nm in self.theaters:
                for mov_no, mov_nm in movies:
                    if stop_event.is_set() or self._held():
                        return False
                    try:
                        shows = cgv_api.fetch_showtimes(mov_no, site_no, ymd)
                    except Exception:  # noqa: BLE001
                        time.sleep(0.4)
                        continue
                    # 요청 분산(차단 회피)
                    time.sleep(0.25 + random.uniform(0.0, 0.2))
                    for s in shows:
                        label = cgv_api.stage_event_label(s.raw)
                        if not label:
                            continue
                        key = f"{mov_no}|{site_no}|{ymd}|{s.time}|{s.scns_no}"
                        if key in self.seen:
                            continue
                        self.seen.add(key)
                        log(f"[무대인사] 🎤 발견: {mov_nm} / {site_nm} / {disp} {s.time} "
                            f"{s.screen} 잔여{s.remaining} — {label}")
                        self._notify("stage_event", mov_nm, site_nm, disp, s.time,
                                     f"{s.screen} · {label}", f"무대인사 감지: {label}", "")
                        # 자동 좌석 잡기(잔여석 미상(-1)이거나 충분할 때 시도)
                        if s.remaining < 0 or s.remaining >= need:
                            log(f"[무대인사] 좌석 잡기 시도: {mov_nm} {s.time}")
                            lock = self.hub.book_lock if self.hub is not None else _NULL_LOCK
                            with lock:
                                if self._held():
                                    return False
                                ok, seat, msg = self.grabber.replay(
                                    self.recipe, day=disp, hhmm=s.time, movie=mov_nm,
                                    persons=persons, preferred=preferred,
                                    only_preferred=only, prefer=prefer)
                            if ok:
                                if self.hub is not None:
                                    self.hub.held.set()
                                log(f"[무대인사] ✅ 좌석 선점: {mov_nm} {seat} — {msg}. "
                                    f"결제 페이지에서 결제하세요.(감시 종료)")
                                self._notify("seat_held", mov_nm, site_nm, disp, s.time,
                                             s.screen, msg, seat)
                                return True
                            log(f"[무대인사] 미완료: {msg} — 계속 감시")
                        else:
                            log(f"[무대인사] 잔여 {s.remaining}석 < 필요 {need}석 — 알림만")
        return False

    def _notify(self, kind, movie, theater, disp, hhmm, screen, status, seat) -> None:
        if not self.notifier:
            return
        try:
            self.notifier.notify_showtime(
                kind=kind, target_name="무대인사", movie=movie, theater=theater,
                date=disp, showtime=hhmm, screen=screen, status_text=status,
                seat_info=seat, booking_url="https://cgv.co.kr/cnm/movieBook/cinema")
        except Exception:  # noqa: BLE001
            pass

    def _sleep(self, seconds: int, stop_event) -> None:
        waited = 0.0
        while waited < seconds and not stop_event.is_set() and not self._held():
            time.sleep(0.5)
            waited += 0.5

    def close(self) -> None:
        if self.grabber and self.hub is None:   # 허브 소유 브라우저는 허브가 닫는다
            try:
                self.grabber.close()
            except Exception:  # noqa: BLE001
                pass
