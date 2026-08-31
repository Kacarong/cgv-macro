"""
클릭 좌표 '녹화기' — 예매 클릭 순서를 그대로 기록해 재생용 레시피를 만든다.

동작:
  1) 설치된 크롬을 (로그인 세션 저장 프로필로) 띄운다.
  2) 사장님이 CGV에 로그인(최초 1회) 후, 예매를 원하는 회차의 '좌석 선택완료'까지 직접 클릭.
  3) 그동안 모든 클릭의 좌표/요소정보를 기록 → recipe.json 저장.

준비:
    pip install playwright
    (크롬이 설치돼 있어야 함 — 별도 크로미움 설치 불필요)

실행:
    python record.py
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

# 모든 프레임에서 클릭을 잡아 window.__rec 로 넘기는 스크립트(캡처 단계)
INIT = r"""
window.__recOn = window.__recOn || false;
document.addEventListener('click', function(e){
  if(!window.__recOn) return;
  try{
    var t = e.target || {};
    var cls = (typeof t.className === 'string') ? t.className : '';
    window.__rec({
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
            if state["recording"]:
                data["n"] = len(steps) + 1
                steps.append(data)
                print(f"  [{data['n']:02d}] click ({data['x']},{data['y']}) "
                      f"{'SEAT ' if data.get('seat') else ''}<{data['tag']}> "
                      f"{data.get('txt','')[:20]}  @ {data['url'].split('/')[-1][:24]}")

        ctx.expose_binding("__rec", on_rec)
        ctx.add_init_script(INIT)

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(START_URL)

        print("\n[record] 1) 열린 크롬에서 CGV 에 로그인하세요(이미 돼 있으면 통과).")
        input("[record]    로그인 확인했으면 Enter → 여기서부터 '녹화 시작' ")
        # 모든 프레임에서 녹화 켜기
        state["recording"] = True
        try:
            for fr in page.frames:
                fr.evaluate("window.__recOn = true")
        except Exception:  # noqa: BLE001
            pass
        # 이후 새 프레임/네비게이션에도 적용되도록 init 에서 __recOn 참조하지만
        # add_init_script 는 __recOn=false 로 초기화하므로, 네비게이션마다 다시 켜준다.
        page.on("framenavigated", lambda f: _safe_on(f))

        print("[record] 2) 이제 예매를 진행하세요: 지역→극장→날짜→회차→인원→'좌석'→선택완료 까지 클릭.")
        print("[record]    (좌석은 원하는 위치를 클릭하면 그 근처 빈자리를 재생 때 잡습니다)")
        input("[record] 3) '좌석 선택완료'까지 끝냈으면 Enter → '녹화 종료' ")
        state["recording"] = False

        recipe = {
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "start_url": START_URL,
            "viewport": {"w": page.viewport_size["width"] if page.viewport_size else 1440,
                         "h": page.viewport_size["height"] if page.viewport_size else 960},
            "steps": steps,
        }
        with open(RECIPE, "w", encoding="utf-8") as f:
            json.dump(recipe, f, ensure_ascii=False, indent=2)
        print(f"\n[record] 저장 완료: {RECIPE}  (클릭 {len(steps)}개)")
        print("[record] 이 recipe.json 파일을 개발자에게 보내주세요.")
        input("[record] Enter 로 종료... ")
        ctx.close()
    return 0


def _safe_on(frame) -> None:
    try:
        frame.evaluate("window.__recOn = true")
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    raise SystemExit(main())
