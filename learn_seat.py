"""
좌석 화면 '학습' 도구 (자동 클릭 기능 완성을 위한 1회용).

이 스크립트는 당신의 크롬을 띄우고, 당신이 CGV 에 로그인해서 '좌석 선택' 화면까지
직접 이동한 뒤 Enter 를 누르면, 그 좌석 화면의 구조(좌석 요소/클래스/속성)와
스크린샷을 파일로 저장합니다. 그 파일을 개발자에게 보내면 자동 클릭 좌표를 확정합니다.

준비:
    pip install playwright
    python -m playwright install chromium      # (크롬 미설치 시에만 필요)

실행:
    python learn_seat.py

진행:
    1) 열리는 크롬에서 CGV 로그인.
    2) 원하는 영화 → 극장 → 날짜 → 시간(회차) → '좌석 선택' 화면까지 이동.
    3) 좌석 배치도가 보이면, 이 터미널로 돌아와 Enter.
    4) 같은 폴더에 seat_capture.json + seat_capture.png 생성 → 그 둘을 보내주세요.
"""
from __future__ import annotations

import json
import os

try:
    from playwright.sync_api import sync_playwright
except Exception:  # noqa: BLE001
    raise SystemExit("먼저 설치하세요:  pip install playwright  그리고  python -m playwright install chromium")

from cgv_macro import paths

PROFILE = os.path.join(paths.data_dir(), "chrome-profile")

# 좌석 화면에서 '좌석처럼 보이는' 후보를 넓게 수집하기 위한 셀렉터들
PROBE_SELECTORS = [
    "svg rect", "svg [class*='seat']", "[class*='seat']", "[class*='Seat']",
    "button[class*='seat']", "[data-seat]", "[data-seatno]", "[data-seat-nm]",
    "g[class*='seat'] rect", "div[class*='seat']", "li[class*='seat']",
    "[class*='cell']", "[role='button'][class*='seat']",
]

DUMP_ATTRS = ["class", "id", "data-seat", "data-seatno", "data-seat-nm",
              "data-seatnm", "aria-label", "title", "fill", "role", "disabled"]


def main() -> int:
    print(f"[learn] 크롬 프로필: {PROFILE}")
    with sync_playwright() as pw:
        # 설치된 크롬을 우선 사용(로그인/안티봇에 유리), 없으면 chromium
        launch_kwargs = dict(
            user_data_dir=PROFILE, headless=False, locale="ko-KR",
            viewport={"width": 1440, "height": 960},
            args=["--start-maximized"],
        )
        try:
            ctx = pw.chromium.launch_persistent_context(channel="chrome", **launch_kwargs)
        except Exception:
            ctx = pw.chromium.launch_persistent_context(**launch_kwargs)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://cgv.co.kr/cnm/movieBook/movie")

        print("\n[learn] 크롬에서 로그인 → 좌석 선택 화면까지 이동하세요.")
        input("[learn] 좌석 배치도가 보이면 여기서 Enter... ")

        result: dict = {"url": page.url, "title": page.title(), "probes": {}}
        # iframe 포함 모든 프레임에서 좌석 후보 탐색
        frames = page.frames
        for fi, fr in enumerate(frames):
            for sel in PROBE_SELECTORS:
                try:
                    loc = fr.locator(sel)
                    c = loc.count()
                except Exception:  # noqa: BLE001
                    continue
                if not c:
                    continue
                samples = []
                for i in range(min(c, 5)):
                    el = loc.nth(i)
                    attrs = {}
                    for a in DUMP_ATTRS:
                        try:
                            v = el.get_attribute(a)
                        except Exception:  # noqa: BLE001
                            v = None
                        if v:
                            attrs[a] = v
                    try:
                        attrs["_text"] = (el.inner_text() or "").strip()[:20]
                    except Exception:  # noqa: BLE001
                        pass
                    samples.append(attrs)
                result["probes"].setdefault(f"frame{fi}", {})[sel] = {"count": c, "samples": samples}

        with open("seat_capture.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        try:
            page.screenshot(path="seat_capture.png", full_page=True)
        except Exception:  # noqa: BLE001
            page.screenshot(path="seat_capture.png")

        print("\n[learn] 저장 완료:")
        print("   -", os.path.abspath("seat_capture.json"))
        print("   -", os.path.abspath("seat_capture.png"))
        print("[learn] 이 두 파일을 개발자에게 보내주세요. (좌석 자동클릭 좌표 확정용)")
        input("[learn] 확인했으면 Enter 로 종료... ")
        ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
