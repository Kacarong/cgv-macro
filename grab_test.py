"""
좌석 자동잡기 '단발 테스트' (녹화 재현형).

먼저 CGV 로그인 화면이 뜹니다 → 로그인하면 극장별 예매로 이동 → 녹화(recipe)를 재현.
날짜/시간/인원/좌석만 아래 입력값으로 덮어씁니다. 결제는 결제 페이지까지만(선점).

준비:  record.py 로 '극장 선택부터 선택완료까지' 한 번 녹화(recipe.json) 필요.
실행:  run_grabtest.bat 더블클릭  (또는 python grab_test.py)
"""
from __future__ import annotations

import json
import os

from cgv_macro import paths
from cgv_macro.logger import setup_logger
from cgv_macro.replayer import Grabber, RECIPE


def main() -> int:
    logger = setup_logger(paths.logs_dir(), "INFO")
    if not os.path.exists(RECIPE):
        print(f"[!] 녹화 파일이 없습니다: {RECIPE}")
        print("    먼저 run_record.bat 로 '극장 선택부터 선택완료까지' 한 번 녹화하세요.")
        input("Enter 로 종료... ")
        return 1
    recipe = json.load(open(RECIPE, encoding="utf-8"))
    print(f"[i] 녹화 불러옴: 클릭 {len(recipe.get('steps', []))}개")

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
    seats_in = input("원하는 좌석(쉼표, 예: E9,E10) [비우면 아무거나]: ").strip()
    preferred = [s.strip() for s in seats_in.split(",") if s.strip()]
    prefer = input("좌석 위치 center/front/back/any [기본 center]: ").strip() or "center"

    g = Grabber(headless=False).__enter__()
    try:
        print("\n[i] 크롬에 로그인 화면이 뜹니다. 로그인하세요(이미 로그인돼 있으면 자동 통과).")
        if not g.ensure_login(wait_manual=True, timeout_s=300):
            print("[!] 로그인/극장별예매 진입 실패. 다시 시도하세요.")
            input("Enter 로 종료... "); return 1
        print("[i] 로그인 확인 → 녹화 재현 시작.")
        ok, seat, msg = g.replay(recipe, day=day, hhmm=hhmm, movie=movie,
                                 persons=persons, preferred=preferred, prefer=prefer)
        print(f"\n결과: {'성공' if ok else '실패'} | 좌석: {seat} | {msg}")
        if ok:
            print("→ 결제 페이지에서 멈췄습니다(좌석 선점). 카드 결제만 직접 하세요.")
        else:
            print(f"→ 실패. {paths.data_dir()} 의 grab_*.png 를 보내주세요.")
        input("Enter 로 종료... ")
    finally:
        g.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
