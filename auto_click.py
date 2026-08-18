"""
CGV 좌석 자동 클릭 실행기 (감지 → 자동 예매/좌석 홀드).

무인증 API 로 대상 회차의 좌석을 감지하다가, 예매 가능해지는 순간
로그인된 크롬으로 예매 흐름을 진행해 좌석을 자동 클릭·홀드하고 디스코드로 알립니다.
결제는 하지 않습니다(홀드까지만).

준비:
    pip install -r requirements.txt playwright
    python -m playwright install chromium        # (크롬 있으면 생략 가능)
    python learn_seat.py                          # 최초 1회 CGV 로그인(세션 저장)

실행:
    python auto_click.py --count 2 --prefer center
      (감시 대상은 config.yaml 의 targets[0] 사용. GUI/CLI 로 먼저 설정 저장)
"""
from __future__ import annotations

import argparse
import logging
import time

from cgv_macro.config import load_config, ConfigError
from cgv_macro.logger import setup_logger
from cgv_macro import cgv_api, paths
from cgv_macro.monitor import _scnymd, _in_range, _bookable
from cgv_macro.notifier import DiscordNotifier


def main() -> int:
    ap = argparse.ArgumentParser(description="CGV 좌석 자동 클릭")
    ap.add_argument("--config", "-c", default=paths.config_path())
    ap.add_argument("--count", type=int, default=2, help="잡을 좌석 수")
    ap.add_argument("--prefer", default="center", choices=["center", "front", "back", "any"])
    ap.add_argument("--seats", default="", help="선호 좌석(쉼표, 예: H10,H11)")
    ap.add_argument("--interval", type=int, default=30, help="감지 주기(초)")
    args = ap.parse_args()

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"[설정 오류] {e}"); return 2

    logger = setup_logger(cfg.logging["dir"], "INFO")
    target = cfg.targets[0]
    preferred = [s.strip() for s in args.seats.split(",") if s.strip()]
    notifier = DiscordNotifier(cfg.discord["webhook_url"], cfg.discord.get("mention", ""))

    mov_no, mov_nm = cgv_api.resolve_movie(target.movie, target.movie_code)
    site_no, site_nm, region = cgv_api.resolve_theater(target.theater, target.theater_code)
    logger.info("자동클릭 대상: %s / %s / %s %s~%s (좌석 %d, %s)",
                mov_nm, site_nm, target.date, target.time_from, target.time_to,
                args.count, args.prefer)

    from cgv_macro.cgv_browser import Booker
    booker = Booker(headless=False).__enter__()
    if not booker.is_logged_in():
        logger.warning("로그인 세션이 없습니다.먼저 'python learn_seat.py' 로 로그인하세요.")
    try:
        while True:
            try:
                shows = cgv_api.fetch_showtimes(mov_no, site_no, _scnymd(target.date))
            except Exception as e:  # noqa: BLE001
                logger.error("조회 실패: %s", e); time.sleep(args.interval); continue

            cands = [s for s in shows
                     if _in_range(s.time, target.time_from, target.time_to)
                     and (not target.screen_type
                          or target.screen_type.lower() in (s.screen + " " + s.fmt).lower())
                     and _bookable(s, args.count)]
            if cands:
                s = cands[0]
                logger.info("예매가능 감지: %s %s 잔여%d — 좌석 잡기 시도", s.time, s.screen, s.remaining)
                ok, seat_str, msg = booker.grab(
                    movie=mov_nm, theater=target.theater or site_nm, day=target.date,
                    hhmm=s.time, count=args.count, prefer=args.prefer,
                    preferred=preferred, screen_type=target.screen_type, region=region)
                if ok:
                    notifier.notify_showtime(
                        kind="seat_held", target_name=target.name, movie=mov_nm,
                        theater=site_nm, date=target.date, showtime=s.time,
                        screen=f"{s.screen} ({s.fmt})", status_text=msg,
                        booking_url="https://cgv.co.kr/cnm/movieBook/movie",
                        seat_info=seat_str)
                    logger.info("완료 — 좌석 홀드됨(%s). 브라우저에서 결제하세요. 종료.", seat_str)
                    input("Enter 로 종료(홀드 유지하려면 결제 먼저)... ")
                    break
                else:
                    logger.warning("좌석 잡기 실패: %s — 계속 감시", str(msg).splitlines()[0])
            else:
                logger.info("아직 예매가능 회차 없음 — 대기")
            time.sleep(args.interval)
    finally:
        booker.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
