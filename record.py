"""
클릭 좌표 '녹화기' — 예매 클릭 순서를 그대로 기록해 재생용 레시피를 만든다.

동작:
  1) 설치된 크롬을 (로그인 세션 저장 프로필로) 띄운다.
  2) 로그인 후, 예매를 원하는 회차의 '좌석 선택완료'까지 직접 클릭.
  3) 모든 클릭의 좌표/요소정보를 기록 → recipe.json 저장.

준비:  pip install playwright   (크롬 설치돼 있으면 크로미움 별도 설치 불필요)
실행:  python record.py
"""
from __future__ import annotations

import json
import os
import time

try:
    from playwright.sync_api import sync_playwright
except Exception:  # noqa: BLE001
    raise SystemExit("먼저 설치: pip install playwright")

from cgv_macro import paths

PROFILE = os.path.join(paths.data_dir(), "chrome-profile")
RECIPE = os.path.join(paths.data_dir(), "recipe.json")
START_URL = "https://cgv.co.kr/cnm/movieBook/cinema"
# 로그인 화면을 먼저 띄운다. 로그인하면 returnUrl 로 자동 이동(극장별 예매).
LOGIN_URL = "https://cgv.co.kr/mem/login?returnUrl=%2Fcnm%2FmovieBook%2Fcinema"

# 모든 프레임에서 클릭을 항상 window.__rec 로 넘긴다(녹화 여부는 파이썬이 판단).
INIT = r"""
document.addEventListener('click', function(e){
  try{
    var t = e.target || {};
    var cls = (typeof t.className === 'string') ? t.className : '';
    window.__rec && window.__rec({
      x: Math.round(e.clientX), y: Math.round(e.clientY),
      url: location.href,
      tag: t.tagName || '',
      cls: cls.replace(/\s+/g,' ').trim().slice(0,100),
      txt: (t.innerText || t.textContent || '').trim().slice(0,30),
      seat: /seatMap_seatNumber/.test(cls),
      w: window.innerWidth, h: window.innerHeight
    });
  }catch(err){}
}, true);
"""


def main() -> int:
    steps: list[dict] = []
    state = {"recording": False}

    print(f"[record] 크롬 프로필: {PROFILE}")
    with sync_playwright() as pw:
        kw = dict(user_data_dir=PROFILE, headless=False, locale="ko-KR",
                  viewport={"width": 1440, "height": 960}, args=["--start-maximized"])
        try:
            ctx = pw.chromium.launch_persistent_context(channel="chrome", **kw)
        except Exception:  # noqa: BLE001
            ctx = pw.chromium.launch_persistent_context(**kw)

        def on_rec(source, data):
            if not state["recording"]:
                return
            data["n"] = len(steps) + 1
            steps.append(data)
            print(f"  [{data['n']:02d}] click ({data['x']},{data['y']}) "
                  f"{'SEAT ' if data.get('seat') else ''}<{data['tag']}> "
                  f"{(data.get('txt') or '')[:20]}  @ {data['url'].split('/')[-1][:24]}")

        ctx.expose_binding("__rec", on_rec)
        ctx.add_init_script(INIT)
        # 새 탭이 열려도 그 탭 클릭까지 잡히도록(바인딩/스크립트는 컨텍스트 전역)
        ctx.on("page", lambda pg: None)

        # 여분 탭 정리 후 한 탭만 사용, 극장별 예매로 이동(+reload 로 스크립트 주입)
        pages = ctx.pages
        page = pages[0] if pages else ctx.new_page()
        for extra in pages[1:]:
            try:
                extra.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
        except Exception:  # noqa: BLE001
            pass
        page.bring_to_front()

        print("\n[record] 1) 열린 크롬의 '로그인' 화면에서 CGV 에 로그인하세요.")
        print("[record]    로그인하면 자동으로 '극장별 예매' 화면으로 넘어갑니다(이미 로그인돼 있으면 바로 이동).")
        input("[record] 2) 극장별 예매 화면이 뜨면 Enter → 여기서부터 '녹화 시작' ")

        state["recording"] = True
        print("[record] ▶ 녹화 시작! 클릭할 때마다 아래에 [01],[02]... 가 찍혀야 정상입니다.")
        print("[record] 2) 예매 진행: 극장→날짜→회차→인원→'좌석'→선택완료 까지 클릭.")
        input("[record] 3) '좌석 선택완료'까지 끝냈으면 Enter → '녹화 종료' ")
        state["recording"] = False

        vp = page.viewport_size or {"width": 1440, "height": 960}
        recipe = {
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "start_url": (steps[0]["url"] if steps else START_URL),
            "viewport": {"w": vp["width"], "h": vp["height"]},
            "steps": steps,
        }
        with open(RECIPE, "w", encoding="utf-8") as f:
            json.dump(recipe, f, ensure_ascii=False, indent=2)
        print(f"\n[record] 저장 완료: {RECIPE}  (클릭 {len(steps)}개)")
        if not steps:
            print("[record] ⚠ 클릭이 하나도 안 잡혔습니다. 크롬을 완전히 닫고 다시 시도해주세요.")
        else:
            print("[record] 이 recipe.json 파일을 개발자에게 보내주세요.")
        input("[record] Enter 로 종료... ")
        ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
