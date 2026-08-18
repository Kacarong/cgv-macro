"""
예매 흐름 '단계별 학습' 도구 (자동 클릭 완성을 위한 1회용).

당신의 크롬을 띄우고, 당신이 CGV 예매를 직접 진행하는 동안 각 단계에서 Enter 를
누르면 그 화면의 버튼/좌석 구조를 저장합니다. 이 파일로 자동 클릭을 완성합니다.

준비:
    pip install playwright
    python -m playwright install chromium   # (크롬 미설치 시에만)

실행:
    python learn_seat.py

진행(각 단계에서 화면이 뜨면 이 터미널로 와서 Enter):
    [1] 로그인 후, 영화·극장·날짜를 고르면 나오는 '시간(회차) 목록' 화면 → Enter
    [2] 회차를 클릭하면 나오는 '인원 선택'(일반/청소년 수) 화면 → Enter
    [3] '좌석 선택'(좌석 배치도) 화면 → Enter
    끝나면 q 입력.
결과 seat_capture.json 을 보내주세요.
"""
from __future__ import annotations

import json
import os

try:
    from playwright.sync_api import sync_playwright
except Exception:  # noqa: BLE001
    raise SystemExit("먼저 설치하세요:  pip install playwright  &&  python -m playwright install chromium")

from cgv_macro import paths

PROFILE = os.path.join(paths.data_dir(), "chrome-profile")
DUMP_ATTRS = ["class", "id", "data-seat", "data-seatno", "data-seat-nm", "aria-label",
              "title", "fill", "role", "disabled", "type"]


def _dump_frame(fr) -> dict:
    out: dict = {"buttons": [], "seat_probe": {}}
    # 모든 버튼(텍스트+클래스 앞부분) — 시간/인원/확정 버튼 파악용
    try:
        btns = fr.locator("button, a[role='button'], [role='button']")
        n = btns.count()
        for i in range(min(n, 80)):
            el = btns.nth(i)
            try:
                txt = (el.inner_text() or "").strip()[:24]
                cls = (el.get_attribute("class") or "").strip().split()
                cls = [c for c in cls if c][:3]
            except Exception:  # noqa: BLE001
                continue
            if txt or cls:
                out["buttons"].append({"text": txt, "class": cls})
    except Exception:  # noqa: BLE001
        pass
    # 좌석 후보
    for sel in ["button[class*='seatMap_seatNumber']", "[class*='seatNormal']",
                "[class*='seatDisabled']", "[class*='seatSold']", "[class*='seatSelected']",
                "button[class*='seat']"]:
        try:
            loc = fr.locator(sel)
            c = loc.count()
        except Exception:  # noqa: BLE001
            continue
        if not c:
            continue
        samples = []
        for i in range(min(c, 4)):
            el = loc.nth(i)
            attrs = {}
            for a in DUMP_ATTRS:
                try:
                    v = el.get_attribute(a)
                except Exception:  # noqa: BLE001
                    v = None
                if v:
                    attrs[a] = v[:120]
            try:
                attrs["_text"] = (el.inner_text() or "").strip()[:16]
            except Exception:  # noqa: BLE001
                pass
            samples.append(attrs)
        out["seat_probe"][sel] = {"count": c, "samples": samples}
    return out


def main() -> int:
    print(f"[learn] 크롬 프로필: {PROFILE}")
    captures = []
    with sync_playwright() as pw:
        kw = dict(user_data_dir=PROFILE, headless=False, locale="ko-KR",
                  viewport={"width": 1440, "height": 960}, args=["--start-maximized"])
        try:
            ctx = pw.chromium.launch_persistent_context(channel="chrome", **kw)
        except Exception:
            ctx = pw.chromium.launch_persistent_context(**kw)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://cgv.co.kr/cnm/movieBook/movie")

        print("\n[learn] 각 단계 화면이 뜨면 Enter, 끝나면 q 를 입력하세요.")
        stage = 0
        while True:
            ans = input(f"[learn] 단계 {stage+1} 저장(Enter) / 종료(q): ").strip().lower()
            if ans == "q":
                break
            stage += 1
            frames = page.frames
            cap = {"stage": stage, "url": page.url, "frames": []}
            for fr in frames:
                try:
                    cap["frames"].append(_dump_frame(fr))
                except Exception:  # noqa: BLE001
                    pass
            captures.append(cap)
            try:
                page.screenshot(path=f"seat_capture_stage{stage}.png", full_page=True)
            except Exception:  # noqa: BLE001
                pass
            print(f"   저장됨: 단계 {stage} (seat_capture_stage{stage}.png)")

        with open("seat_capture.json", "w", encoding="utf-8") as f:
            json.dump(captures, f, ensure_ascii=False, indent=2)
        print("\n[learn] 저장 완료 →", os.path.abspath("seat_capture.json"))
        print("[learn] seat_capture.json 과 seat_capture_stage*.png 들을 보내주세요.")
        ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
