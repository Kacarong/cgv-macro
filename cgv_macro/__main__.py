"""CLI 진입점.  실행: python -m cgv_macro --config config.yaml"""
from __future__ import annotations

import argparse
import sys

from .config import load_config, ConfigError
from .logger import setup_logger


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cgv_macro",
        description="CGV 상영 오픈/취소표 감지 + 디스코드 알림",
    )
    from .paths import config_path
    parser.add_argument("--config", "-c", default=config_path(),
                        help="설정 파일 경로(기본: 앱 데이터 폴더의 config.yaml)")
    parser.add_argument("--once", action="store_true",
                        help="1회만 폴링하고 종료(테스트용)")
    parser.add_argument("--check-login", action="store_true",
                        help="로그인 세션이 살아있는지 확인만 하고 종료")
    parser.add_argument("--test-discord", action="store_true",
                        help="디스코드 웹훅으로 테스트 메시지 전송")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"[설정 오류] {e}", file=sys.stderr)
        return 2

    logger = setup_logger(config.logging["dir"], config.logging["level"])
    logger.info("cgv_macro 시작 (config=%s)", args.config)

    if args.test_discord:
        from .notifier import DiscordNotifier
        n = DiscordNotifier(config.discord["webhook_url"], config.discord.get("mention", ""))
        ok = n.notify_error("테스트 메시지입니다. 웹훅이 정상 동작합니다.", cooldown_seconds=0)
        print("전송 성공" if ok else "전송 실패")
        return 0 if ok else 1

    if args.check_login:
        from .cgv import CgvClient
        with CgvClient(config.browser) as c:
            ok = c.is_logged_in()
        print("로그인 세션: " + ("정상(로그인됨)" if ok else "없음 → login_setup.py 로 로그인하세요"))
        return 0 if ok else 1

    if args.once:
        from .cgv import CgvClient
        from .notifier import DiscordNotifier
        from .state import StateStore
        from .monitor import _poll_once
        n = DiscordNotifier(config.discord["webhook_url"], config.discord.get("mention", ""))
        from .paths import state_path
        st = StateStore(state_path())
        with CgvClient(config.browser) as c:
            _poll_once(c, config, st, n)
        st.save()
        logger.info("1회 폴링 완료")
        return 0

    from .monitor import run
    run(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
