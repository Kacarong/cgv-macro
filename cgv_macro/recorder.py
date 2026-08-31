"""
녹화 로직(콘솔/GUI 공용). 클릭을 localStorage 에 쌓고 파이썬이 폴링해 recipe.json 저장.
"""
from __future__ import annotations

import json
import logging
import os
import time

from playwright.sync_api import sync_playwright

from . import paths

logger = logging.getLogger("cgv_macro")

PROFILE = os.path.join(paths.data_dir(), "chrome-profile")
RECIPE = os.path.join(paths.data_dir(), "recipe.json")
LOGIN_URL = "https://cgv.co.kr/mem/login?returnUrl=%2Fcnm%2FmovieBook%2Fcinema"
START_URL = "https://cgv.co.kr/cnm/movieBook/cinema"

INIT = r"""
document.addEventListener('click', function(e){
  try{
    var arr = JSON.parse(localStorage.getItem('__rec') || '[]');
    var t = e.target || {};
    var cls = (typeof t.className === 'string') ? t.className : '';
    arr.push({x:Math.round(e.clientX), y:Math.round(e.clientY), url:location.href,
      tag:t.tagName||'', cls:cls.replace(/\s+/g,' ').trim().slice(0,100),
      txt:(t.innerText||t.textContent||'').trim().slice(0,30),
      seat:/seatMap_seatNumber/.test(cls), w:window.innerWidth, h:window.innerHeight});
    localStorage.setItem('__rec', JSON.stringify(arr));
  }catch(err){}
}, true);
"""


def run_recorder(start_event, stop_event, log=None) -> int:
    """
    start_event: '녹화 시작'(로그인 후) 신호. stop_event: '녹화 종료' 신호.
    두 이벤트는 GUI 버튼/콘솔에서 set 한다. recipe.json 저장 후 종료.
    """
    log = log or logger.info
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
            page.wait_for_timeout(1200)
        except Exception:  # noqa: BLE001
            pass
        page.bring_to_front()
        log("로그인 후 '녹화 시작'을 누르세요.")

        # 녹화 시작 대기(브라우저 이벤트 계속 처리)
        while not start_event.is_set() and not stop_event.is_set():
            page.wait_for_timeout(200)
        if stop_event.is_set():
            ctx.close(); return 1
        try:
            page.evaluate("() => localStorage.setItem('__rec','[]')")
        except Exception:  # noqa: BLE001
            pass
        log("▶ 녹화 중… 예매를 진행하세요(극장→날짜→회차→인원→좌석→선택완료→결제하기).")

        printed = 0
        last = []
        while not stop_event.is_set():
            try:
                arr = page.evaluate("() => JSON.parse(localStorage.getItem('__rec') || '[]')") or []
            except Exception:  # noqa: BLE001
                arr = None
            if arr is not None:
                last = arr
                if len(arr) > printed:
                    for i in range(printed, len(arr)):
                        d = arr[i]
                        log(f"  [{i+1:02d}] {'SEAT ' if d.get('seat') else ''}{(d.get('txt') or '')[:18]}")
                    printed = len(arr)
            page.wait_for_timeout(400)

        steps = last
        try:
            steps = page.evaluate("() => JSON.parse(localStorage.getItem('__rec') || '[]')") or last
        except Exception:  # noqa: BLE001
            pass
        vp = page.viewport_size or {"width": 1440, "height": 960}
        recipe = {"created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                  "start_url": (steps[0]["url"] if steps else START_URL),
                  "viewport": {"w": vp["width"], "h": vp["height"]}, "steps": steps}
        with open(RECIPE, "w", encoding="utf-8") as f:
            json.dump(recipe, f, ensure_ascii=False, indent=2)
        log(f"녹화 저장 완료: 클릭 {len(steps)}개 → {RECIPE}")
        ctx.close()
    return 0
