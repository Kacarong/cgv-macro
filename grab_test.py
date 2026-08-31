"""
좌석 자동잡기 '단발 테스트' — 지금 예매 가능한 회차로 좌석잡기가 되는지 확인.

준비:  pip install playwright   +  record.py 로 CGV 로그인 + 센텀시티 선택 1회
실행:  python grab_test.py   (또는 run_grabtest.bat 더블클릭)

주의: 크롬은 record.py 와 같은 프로필을 씁니다. record.py 창은 닫고 실행하세요.
결제는 안 하고 '좌석 홀드'까지만 진행합니다.
"""
from __future__ import annotations

from cgv_macro import paths
from cgv_macro.logger import setup_logger
from cgv_macro.replayer import Grabber


def main() -> int:
    logger = setup_logger(paths.logs_dir(), "INFO")
    print("=== 좌석 자동잡기 테스트 (오디세이/센텀시티 기준) ===")
    movie = input("영화명 [기본: 오디세이]: ").strip() or "오디세이"
    day = input("날짜 YYYY-MM-DD (예: 2026-09-04): ").strip()
    hhmm = input("회차 시간 HH:MM (예: 20:00): ").strip()

    def _num(prompt, default=0):
        try:
            return int(input(prompt).strip() or str(default))
        except ValueError:
            return default
    print("인원 구성(0이면 없음):")
    persons = {
        "일반": _num("  일반(성인) 수 [기본 2]: ", 2),
        "청소년": _num("  청소년 수 [기본 0]: ", 0),
        "우대": _num("  우대 수 [기본 0]: ", 0),
    }
    prefer = input("좌석 위치 center/front/back/any [기본 center]: ").strip() or "center"

    g = Grabber(headless=False).__enter__()
    try:
        ok, seat, msg = g.grab(movie=movie, day=day, hhmm=hhmm, persons=persons, prefer=prefer)
        print(f"\n결과: {'성공' if ok else '실패'} | 좌석: {seat} | {msg}")
        if ok:
            print("→ 브라우저에 좌석이 홀드됐습니다. 결제는 직접 하세요(약 10분 안).")
            input("Enter 로 종료(닫으면 홀드 풀림)... ")
        else:
            print("→ 실패. 앱데이터 폴더의 grab_*.png 스크린샷을 개발자에게 보내주세요:")
            print("  ", paths.data_dir())
            input("Enter 로 종료... ")
    finally:
        g.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
