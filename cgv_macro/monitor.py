"""폴링 루프: 감시 → 상태비교 → 디스코드 알림.

CGV 무인증 API(cgv_api)를 폴링한다. 로그인/브라우저 불필요.
알림 종류: open(상영 오픈), available(잔여석), cancel(매진→잔여, 취소표).
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any

from . import cgv_api
from .cgv_api import Showtime
from .config import Config, Target
from .notifier import DiscordNotifier
from .state import StateStore
from . import paths

logger = logging.getLogger("cgv_macro")

BOOKING_URL = "https://cgv.co.kr/cnm/movieBook/movie"


def _scnymd(date: str) -> str:
    """'2026-08-19' → '20260819'."""
    return date.replace("-", "").replace(".", "").strip()


def _bookable(s: Showtime, min_remaining: int) -> bool:
    return (not s.soldout) and (not s.controlled) and s.remaining >= min_remaining


def _status_of(s: Showtime, min_remaining: int) -> str:
    if s.soldout or s.controlled or s.remaining == 0:
        return "soldout"
    if s.remaining >= min_remaining:
        return "available"
    return "open"


def _status_text(s: Showtime) -> str:
    if s.controlled:
        return "판매통제"
    if s.soldout or s.remaining == 0:
        return "매진"
    tot = f"/{s.total}" if s.total > 0 else ""
    return f"잔여 {s.remaining}{tot}석"


def _decide_kinds(is_new: bool, prev_status: str, cur_status: str, s: Showtime,
                  alerts: dict[str, Any], notified: set[str]) -> list[str]:
    min_remaining = int(alerts.get("min_remaining_seats", 1))
    kinds: list[str] = []
    if is_new and alerts.get("on_showtime_open", True):
        kinds.append("open")
    if cur_status == "available" and s.remaining >= min_remaining:
        if prev_status == "soldout" and alerts.get("on_soldout_to_available", True) \
                and "cancel" not in notified:
            kinds.append("cancel")
        elif alerts.get("on_seats_available", True) and "available" not in notified:
            kinds.append("available")
    if ("available" in kinds or "cancel" in kinds) and "open" in kinds:
        kinds.remove("open")
    return kinds


def run(config: Config, stop_event=None) -> None:
    notifier = DiscordNotifier(
        config.discord["webhook_url"], config.discord.get("mention", "")
    )
    state = StateStore(paths.state_path())
    err = config.errors
    interval = int(config.poll["interval_seconds"])
    jitter = int(config.poll["jitter_seconds"])
    cache: dict[str, tuple[str, str, str, str]] = {}  # target.key() → (movNo,movNm,siteNo,siteNm)

    consecutive_failures = 0
    logger.info("감시 시작 — 대상 %d개, 주기 %ds(+지터 %ds)",
                len(config.targets), interval, jitter)

    def _stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    try:
        while not _stopped():
            cycle_start = time.time()
            try:
                _poll_once(config, state, notifier, cache)
                consecutive_failures = 0
                state.save()
            except KeyboardInterrupt:
                raise
            except Exception as e:  # noqa: BLE001
                consecutive_failures += 1
                logger.error("폴링 실패(%d회째): %s", consecutive_failures, e)
                if consecutive_failures >= int(err["alert_after_consecutive_failures"]):
                    notifier.notify_error(
                        f"연속 {consecutive_failures}회 폴링 실패: {e}",
                        cooldown_seconds=int(err["error_alert_cooldown_seconds"]),
                    )

            sleep_for = interval + random.uniform(0, max(0, jitter))
            elapsed = time.time() - cycle_start
            sleep_for = max(1.0, sleep_for - elapsed)
            waited = 0.0
            while waited < sleep_for and not _stopped():
                time.sleep(min(0.5, sleep_for - waited))
                waited += 0.5
        if _stopped():
            logger.info("중지 요청 — 감시를 종료합니다.")
    except KeyboardInterrupt:
        logger.info("사용자 중단(Ctrl+C) — 종료합니다.")
    finally:
        state.save()


def _resolve(target: Target, cache: dict) -> tuple[str, str, str, str] | None:
    tkey = target.key()
    if tkey in cache:
        return cache[tkey]
    try:
        mov_no, mov_nm = cgv_api.resolve_movie(target.movie, target.movie_code)
        site_no, site_nm = cgv_api.resolve_theater(target.theater, target.theater_code)
        cache[tkey] = (mov_no, mov_nm, site_no, site_nm)
        logger.info("[%s] 해석: 영화 %s(%s) / 극장 %s(%s)",
                    target.name, mov_nm, mov_no, site_nm, site_no)
        return cache[tkey]
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s] 영화/극장 코드 해석 실패: %s", target.name, e)
        return None


def _in_range(hhmm: str, start: str, end: str) -> bool:
    def m(x: str) -> int:
        try:
            h, mm = x.split(":"); return int(h) * 60 + int(mm)
        except Exception:  # noqa: BLE001
            return -1
    v = m(hhmm)
    return v >= 0 and m(start) <= v <= m(end)


def _poll_once(config: Config, state: StateStore, notifier: DiscordNotifier,
               cache: dict) -> None:
    alerts = config.alerts
    min_remaining = int(alerts.get("min_remaining_seats", 1))

    for target in config.targets:
        resolved = _resolve(target, cache)
        if not resolved:
            continue
        mov_no, mov_nm, site_no, site_nm = resolved
        tkey = target.key()

        shows = cgv_api.fetch_showtimes(mov_no, site_no, _scnymd(target.date))
        # 시간대 + 상영관/포맷 필터
        shows = [
            s for s in shows
            if _in_range(s.time, target.time_from, target.time_to)
            and (not target.screen_type
                 or target.screen_type.lower() in (s.screen + " " + s.fmt).lower())
        ]

        # 최초 폴링: 현재 상태를 조용히 기준선으로 저장(스팸 방지). 요약 1건만.
        if not state.is_seeded(tkey):
            state.mark_seeded(tkey)
            for s in shows:
                cur = _status_of(s, min_remaining)
                notified = {"open"}
                if cur == "available":
                    notified.add("available")
                state.set_showtime(tkey, s.showtime_key(),
                                   {"status": cur, "remaining": s.remaining,
                                    "notified": sorted(notified)})
            if shows:
                avail = [s for s in shows if _bookable(s, min_remaining)]
                logger.info("[%s] 감시 시작 — 회차 %d건, 현재 예매가능 %d건",
                            target.name, len(shows), len(avail))
                notifier.notify_info(
                    f"👀 감시 시작: {target.name}",
                    f"영화 {mov_nm} / {site_nm} / {target.date}\n"
                    f"현재 회차 {len(shows)}건, 예매가능 {len(avail)}건.\n"
                    f"이후 새 상영 오픈·취소표(매진→잔여) 발생 시 알립니다.",
                )
            else:
                logger.info("[%s] 감시 시작 — 아직 미오픈. 열리면 알립니다.", target.name)
            continue

        if not shows:
            logger.info("[%s] 감시 회차 없음(미오픈이거나 필터 결과 0)", target.name)
            continue
        logger.info("[%s] 회차 %d건 확인", target.name, len(shows))

        for s in shows:
            skey = s.showtime_key()
            prev = state.get_showtime(tkey, skey)
            is_new = not prev
            prev_status = prev.get("status", "")
            notified = set(prev.get("notified") or [])
            cur_status = _status_of(s, min_remaining)

            for kind in _decide_kinds(is_new, prev_status, cur_status, s, alerts, notified):
                logger.info("[%s] 알림: %s (%s %s %s, %s)",
                            target.name, kind, s.time, s.screen, s.fmt, _status_text(s))
                notifier.notify_showtime(
                    kind=kind,
                    target_name=target.name,
                    movie=mov_nm,
                    theater=site_nm,
                    date=target.date,
                    showtime=s.time,
                    screen=f"{s.screen} ({s.fmt})" if s.fmt else s.screen,
                    status_text=_status_text(s),
                    booking_url=BOOKING_URL,
                )
                notified.add(kind)
                if kind == "cancel":
                    notified.add("available")  # 취소표=잔여석 → available 중복 방지

            state.set_showtime(tkey, skey, {
                "status": cur_status,
                "remaining": s.remaining,
                "notified": sorted(notified),
            })
