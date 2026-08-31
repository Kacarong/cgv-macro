"""
클릭 좌표 '녹화기' — 예매 클릭 순서를 그대로 기록해 재생용 레시피를 만든다.

클릭은 브라우저(localStorage)에 저장되고, 파이썬이 주기적으로 읽어 화면에 표시한다.
(Playwright 동기 API 에서 input() 대기 중에도 클릭이 유실되지 않도록 하는 방식)

준비:  pip install playwright   (크롬 설치돼 있으면 크로미움 별도 설치 불필요)
실행:  python record.py
"""
from __future__ import annotations

import json
import os
import threading
import time

try:
    from playwright.sync_api import sync_playwright
except Exception:  # noqa: BLE001
    raise SystemExit("먼저 설치: pip install playwright")

from cgv_macro import paths

PROFILE = os.path.join(paths.data_dir(), "chrome-profile")
RECIPE = os.path.join(paths.data_dir(), "recipe.json")
START_URL = "https://cgv.co.kr/cnm/movieBook/cinema"
LOGIN_URL = "https://cgv.co.kr/mem/login?returnUrl=%2Fcnm%2FmovieBook%2Fcinema"

# 클릭을 localStorage['__rec'] 배열에 계속 쌓는다(네비게이션에도 유지, 같은 origin).
INIT = r"""
document.addEventListener('click', function(e){
  try{
    var arr = JSON.parse(localStorage.getItem('__rec') || '[]');
    var t = e.target || {};
    var cls = (typeof t.className === 'string') ? t.className : '';
    arr.push({
      x: Math.round(e.clientX), y: Math.round(e.clientY),
      url: location.href, tag: t.tagName || '',
      cls: cls.replace(/\s+/g,' ').trim().slice(0,100),
      txt: (t.innerText || t.textContent || '').trim().slice(0,30),
      seat: /seatMap_seatNumber/.test(cls),
      w: window.innerWidth, h: window.innerHeight
    });
    localStorage.setItem('__rec', JSON.stringify(arr));
  }catch(err){}
}, true);
"""


def _read_clicks(page):
    try:
        return page.evaluate("() => JSON.parse(localStorage.getItem('__rec') || '[]')") or []
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    print(f"[record] 크롬 프로필: {PROFILE}")
    with sync_playwright() as pw:
        kw = dict(user_data_dir=PROFILE, headless=False, locale="ko-KR",
                  viewport={"width": 1440, "height": 960}, args=["--start-maximized"])
        try:
            ctx = pw.chromium.launch_persistent_context(channel="chrome", **kw)
        except Exception:  # noqa: BLE001
            ctx = pw.chromium.launch_persistent_context(**kw)
        ctx.add_init_script(INIT)

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

        print("\n[record] 1) 크롬 '로그인' 화면에서 CGV 로그인 → 자동으로 극장별 예매로 이동.")
        input("[record] 2) 극장별 예매 화면이 뜨면 Enter → '녹화 시작' ")

        # 녹화 시작: 이전(로그인) 클릭 기록 초기화
        try:
            page.evaluate("() => localStorage.setItem('__rec','[]')")
        except Exception:  # noqa: BLE001
            pass
        print("[record] ▶ 녹화 시작! 클릭하세요(센텀시티→날짜→회차→인원→좌석→선택완료).")
        print("[record]    끝나면 이 창에서 Enter 를 누르세요.\n")

        stop = threading.Event()
        threading.Thread(target=lambda: (input(), stop.set()), daemon=True).start()

        printed = 0
        last = []
        while not stop.is_set():
            arr = _read_clicks(page)
            if arr is not None:
                last = arr
                if len(arr) > printed:
                    for i in range(printed, len(arr)):
                        d = arr[i]
                        print(f"  [{i+1:02d}] click ({d.get('x')},{d.get('y')}) "
                              f"{'SEAT ' if d.get('seat') else ''}<{d.get('tag')}> "
                              f"{(d.get('txt') or '')[:20]}  @ {str(d.get('url','')).split('/')[-1][:22]}")
                    printed = len(arr)
            page.wait_for_timeout(500)

        steps = _read_clicks(page) or last
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
            print("[record] ⚠ 클릭이 하나도 안 잡혔습니다. 알려주세요.")
        else:
            print("[record] 이 recipe.json 을 개발자에게 보내주세요.")
        input("[record] Enter 로 종료... ")
        ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
