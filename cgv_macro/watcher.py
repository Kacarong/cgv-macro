"""
감시: 대상 회차의 취소표/오픈을 무인증 API로 감지 → 재생기로 좌석 자동 잡기.

흐름: 로그인(1회) → 반복 [API로 잔여석 확인 → 예매가능하면 replay 로 좌석 잡기].
원하는 좌석(only_preferred)이면 그 좌석이 뜰 때까지 잡지 않고 계속 감시.
"""
from __future__ import annotations

import logging
import time

from . import cgv_api
from .replayer import Grabber

logger = logging.getLogger("cgv_macro")


def _scnymd(date: str) -> str:
    return (date or "").replace("-", "").replace(".", "").strip()


class Watcher:
    def __init__(self, recipe: dict, target: dict, notifier=None) -> None:
        self.recipe = recipe
        self.t = target
        self.notifier = notifier
        self.grabber: Grabber | None = None

    def run(self, stop_event, log=None) -> None:
        log = log or logger.info
        t = self.t
        try:
            mov_no, mov_nm = cgv_api.resolve_movie(t.get("movie", ""), t.get("movie_code", ""))
            site_no, site_nm, _region = cgv_api.resolve_theater(t.get("theater", ""), t.get("theater_code", ""))
        except Exception as e:  # noqa: BLE001
            log(f"[감시] 영화/극장 해석 실패: {e}")
            return
        target_time = (t.get("time") or "").strip()
        persons = {k: int(v) for k, v in (t.get("persons") or {"일반": 2}).items() if int(v) > 0}
        need = sum(persons.values()) or 1
        preferred = t.get("preferred") or []
        only_pref = bool(t.get("only_preferred"))
        prefer = t.get("prefer", "center")
        interval = max(5, int(t.get("interval", 10)))
        screen_type = (t.get("screen_type") or "").strip()

        log(f"[감시] 대상: {mov_nm} / {site_nm} / {t.get('date')} "
            f"{target_time or '(전체 회차)'} / 좌석 {need}석 "
            f"{'/ 원하는좌석 '+','.join(preferred) if preferred else ''}")

        self.grabber = Grabber(headless=False).__enter__()
        log("[감시] 크롬에 로그인하세요(이미 로그인돼 있으면 자동 통과)...")
        if not self.grabber.ensure_login(timeout_s=600):
            log("[감시] 로그인/극장별예매 진입 실패 — 중지")
            return
        log("[감시] 로그인 확인 → 감시 시작")

        while not stop_event.is_set():
            try:
                shows = cgv_api.fetch_showtimes(mov_no, site_no, _scnymd(t.get("date", "")))
            except Exception as e:  # noqa: BLE001
                log(f"[감시] 조회 오류: {e}")
                self._sleep(interval, stop_event)
                continue

            cands = [
                s for s in shows
                if s.remaining >= need
                and (not target_time or s.time == target_time)
                and (not screen_type or screen_type.lower() in (s.screen + " " + s.fmt).lower())
            ]
            if cands:
                s = cands[0]
                log(f"[감시] 예매가능 감지: {s.time} {s.screen} 잔여{s.remaining} → 좌석 잡기 시도")
                ok, seat, msg = self.grabber.replay(
                    self.recipe, day=t.get("date", ""), hhmm=s.time, movie=mov_nm,
                    persons=persons, preferred=preferred, only_preferred=only_pref, prefer=prefer)
                if ok:
                    log(f"[감시] ✅ 좌석 선점 완료: {seat} — {msg}")
                    if self.notifier:
                        try:
                            self.notifier.notify_showtime(
                                kind="seat_held", target_name=t.get("name", "대상"),
                                movie=mov_nm, theater=site_nm, date=t.get("date", ""),
                                showtime=s.time, screen=f"{s.screen} ({s.fmt})",
                                status_text=msg, seat_info=seat,
                                booking_url="https://cgv.co.kr/cnm/movieBook/cinema")
                        except Exception:  # noqa: BLE001
                            pass
                    break
                else:
                    log(f"[감시] 미완료: {msg}")
            else:
                log(f"[감시] 대기중 — 예매가능 회차 없음")
            self._sleep(interval, stop_event)
        log("[감시] 종료")

    @staticmethod
    def _sleep(seconds: int, stop_event) -> None:
        waited = 0.0
        while waited < seconds and not stop_event.is_set():
            time.sleep(0.5)
            waited += 0.5

    def close(self) -> None:
        if self.grabber:
            try:
                self.grabber.close()
            except Exception:  # noqa: BLE001
                pass
