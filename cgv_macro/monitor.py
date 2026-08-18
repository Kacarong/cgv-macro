"""폴링 루프: 감시 → 상태비교 → 알림 → (옵션)좌석 자동선택."""
from __future__ import annotations

import logging
import random
import time
from typing import Any

from .cgv import CgvClient, Showtime
from .config import Config, Target
from .notifier import DiscordNotifier
from .state import StateStore
from . import paths

logger = logging.getLogger("cgv_macro")


def _status_of(s: Showtime, min_remaining: int) -> str:
    if s.soldout or s.remaining == 0:
        return "soldout"
    if s.remaining >= min_remaining or s.remaining == -1:
        return "available" if s.remaining != -1 else "open"
    return "open"


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

    # 잔여석/취소표 알림이 있으면 중복되는 'open' 은 억제(스팸 방지)
    if ("available" in kinds or "cancel" in kinds) and "open" in kinds:
        kinds.remove("open")
    return kinds


def _status_text(s: Showtime) -> str:
    if s.soldout or s.remaining == 0:
        return "매진"
    if s.remaining > 0:
        tot = f"/{s.total}" if s.total > 0 else ""
        return f"잔여 {s.remaining}{tot}석"
    return "예매 가능(잔여석 수 미상)"


def run(config: Config, stop_event=None) -> None:
    """감시 루프. stop_event(threading.Event)가 set 되면 안전하게 종료."""
    notifier = DiscordNotifier(
        config.discord["webhook_url"], config.discord.get("mention", "")
    )
    state = StateStore(paths.state_path())
    err = config.errors
    interval = int(config.poll["interval_seconds"])
    jitter = int(config.poll["jitter_seconds"])

    consecutive_failures = 0
    logger.info("감시 시작 — 대상 %d개, 주기 %ds(+지터 %ds)",
                len(config.targets), interval, jitter)

    def _stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    client: CgvClient | None = None
    try:
        while not _stopped():
            cycle_start = time.time()
            try:
                if client is None:
                    client = CgvClient(config.browser).__enter__()
                _poll_once(client, config, state, notifier)
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
                # 컨텍스트가 깨졌을 수 있으니 재생성
                try:
                    if client:
                        client.__exit__(None, None, None)
                except Exception:  # noqa: BLE001
                    pass
                client = None

            sleep_for = interval + random.uniform(0, max(0, jitter))
            elapsed = time.time() - cycle_start
            sleep_for = max(1.0, sleep_for - elapsed)
            logger.debug("다음 폴링까지 %.1fs 대기", sleep_for)
            # 중지 신호에 빠르게 반응하도록 잘게 쪼개서 대기
            waited = 0.0
            while waited < sleep_for and not _stopped():
                time.sleep(min(0.5, sleep_for - waited))
                waited += 0.5
        if _stopped():
            logger.info("중지 요청 — 감시를 종료합니다.")
    except KeyboardInterrupt:
        logger.info("사용자 중단(Ctrl+C) — 종료합니다.")
    finally:
        if client:
            try:
                client.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        state.save()


def _poll_once(client: CgvClient, config: Config, state: StateStore,
               notifier: DiscordNotifier) -> None:
    alerts = config.alerts
    auto = config.auto_select
    min_remaining = int(alerts.get("min_remaining_seats", 1))

    for target in config.targets:
        showtimes, booking_url = _fetch_with_retry(client, target, config.errors)
        tkey = target.key()

        if not showtimes:
            logger.info("[%s] 감시 대상 회차 없음(아직 미오픈이거나 필터 결과 0)", target.name)
            continue

        for s in showtimes:
            skey = s.showtime_key()
            prev = state.get_showtime(tkey, skey)
            is_new = not prev
            prev_status = prev.get("status", "")
            notified = set(prev.get("notified") or [])
            cur_status = _status_of(s, min_remaining)

            kinds = _decide_kinds(is_new, prev_status, cur_status, s, alerts, notified)

            for kind in kinds:
                logger.info("[%s] 알림 발생: %s (%s %s, %s)",
                            target.name, kind, s.time, s.screen, _status_text(s))
                notifier.notify_showtime(
                    kind=kind,
                    target_name=target.name,
                    movie=target.movie or target.movie_code,
                    theater=target.theater or target.theater_code,
                    date=target.date,
                    showtime=s.time,
                    screen=s.screen,
                    status_text=_status_text(s),
                    booking_url=s.booking_url or booking_url,
                )
                notified.add(kind)

                # 잔여석/취소표 알림이면서 자동선택이 켜져 있으면 좌석 홀드 시도
                if kind in ("available", "cancel") and auto.get("enabled") \
                        and "seat_held" not in notified:
                    ok, seat_info = client.auto_select_seats(s, auto)
                    if ok:
                        notifier.notify_showtime(
                            kind="seat_held",
                            target_name=target.name,
                            movie=target.movie or target.movie_code,
                            theater=target.theater or target.theater_code,
                            date=target.date,
                            showtime=s.time,
                            screen=s.screen,
                            status_text=_status_text(s),
                            booking_url=s.booking_url or booking_url,
                            seat_info=seat_info,
                        )
                        notified.add("seat_held")

            # 상태 갱신
            state.set_showtime(tkey, skey, {
                "status": cur_status,
                "remaining": s.remaining,
                "notified": sorted(notified),
            })


def _fetch_with_retry(client: CgvClient, target: Target, err: dict[str, Any]):
    last_exc = None
    for attempt in range(int(err["max_retries"]) + 1):
        try:
            return client.fetch_showtimes(target)
        except Exception as e:  # noqa: BLE001
            last_exc = e
            logger.warning("[%s] 조회 실패(%d/%d): %s",
                           target.name, attempt + 1, int(err["max_retries"]) + 1, e)
            time.sleep(int(err["retry_backoff_seconds"]))
    if last_exc:
        raise last_exc
    return [], ""
