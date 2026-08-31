"""
좌석 자동 잡기 '재생기' (녹화 재현형).

record.py 로 만든 recipe.json(클릭 순서)을 그대로 재현한다. 단, 매번 달라지는
부분(날짜/회차시간/좌석/인원)은 대상 값으로 덮어쓴다:
  - 날짜(dayScroll)      → 대상 날짜의 일(day) 버튼
  - 회차(screenInfo_*)   → 대상 영화/시간의 회차
  - 인원(btn-num)        → persons 구성대로
  - 좌석(seatMap/좌석표) → 원하는/빈 좌석
그 외(극장 선택/선택/선택완료/결제하기 등)는 '기록된 요소(텍스트)'를 찾아 그대로 클릭.

로그인은 먼저 CGV 로그인 화면을 띄우고, 로그인되면(returnUrl=극장별예매) 재현을 시작한다.
결제는 하지 않고 '결제하기'로 결제 페이지까지만 진입(좌석 선점).
"""
from __future__ import annotations

import logging
import os
import re
import time

from playwright.sync_api import sync_playwright

from . import paths

logger = logging.getLogger("cgv_macro")

PROFILE = os.path.join(paths.data_dir(), "chrome-profile")
RECIPE = os.path.join(paths.data_dir(), "recipe.json")
LOGIN_URL = "https://cgv.co.kr/mem/login?returnUrl=%2Fcnm%2FmovieBook%2Fcinema"
CINEMA_URL = "https://cgv.co.kr/cnm/movieBook/cinema"

SEL_SEAT_OK = "button[class*='seatMap_seatNumber']:not([class*='seatDisabled'])"
_SEAT_RE = re.compile(r"^[A-Z]{1,2}\d{1,3}$")

# 텍스트로 '보이는' 요소를 찾아 클릭
CLICK_TEXT_JS = r"""(a)=>{
  const norm=s=>(s||'').replace(/\s+/g,' ').trim();
  const target=norm(a.txt); const clsHint=(a.cls||'').split(' ')[0];
  if(!target) return 'empty';
  let cands=[...document.querySelectorAll('button,a,li,span,div,strong,em')].filter(e=>{
    const t=norm(e.textContent); if(!t||!t.includes(target)) return false;
    const r=e.getBoundingClientRect();
    return r.width>0&&r.height>0&&e.offsetParent!==null;
  });
  if(clsHint){const pf=cands.filter(e=>typeof e.className==='string'&&e.className.includes(clsHint));
    if(pf.length) cands=pf;}
  cands.sort((x,y)=>norm(x.textContent).length-norm(y.textContent).length);
  const el=cands[0]; if(!el) return 'nf';
  el.scrollIntoView({block:'center'}); el.click();
  return 'ok:'+norm(el.textContent).slice(0,18);
}"""

MARK_SHOW_JS = r"""(a)=>{const {hhmm,movie}=a;
  const T=[...document.querySelectorAll("[class*='accordionTitle']")];
  const L=[...document.querySelectorAll("[class*='screenInfo_timeLink'],[class*='screenInfo_timeWrap']")];
  const pt=e=>{let b=null;for(const t of T){
    if(t.compareDocumentPosition(e)&Node.DOCUMENT_POSITION_FOLLOWING)b=t;}return b;};
  for(const lk of L){const x=lk.textContent||"";
    if(x.includes(hhmm)&&!x.includes('예매종료')&&!x.includes('준비')){
      const t=pt(lk);
      if(!movie||(t&&t.textContent.includes(movie))){lk.setAttribute('data-ap','1');
        return 'ok:'+x.replace(/\s+/g,' ').slice(0,26);}}}
  return 'nf';}"""


def _classify(step: dict) -> str:
    cls = step.get("cls", "") or ""
    txt = (step.get("txt") or "").strip()
    if "dayScroll" in cls:
        return "date"
    if "screenInfo" in cls:
        return "showtime"
    if "btn-num" in cls:
        return "person"
    if step.get("seat") or _SEAT_RE.match(txt):
        return "seat"
    return "literal"


class Grabber:
    def __init__(self, headless: bool = False) -> None:
        self.headless = headless
        self._pw = None
        self._ctx = None
        self.page = None

    def __enter__(self) -> "Grabber":
        self._pw = sync_playwright().start()
        kw = dict(user_data_dir=PROFILE, headless=self.headless, locale="ko-KR",
                  viewport={"width": 1440, "height": 960}, args=["--start-maximized"])
        try:
            self._ctx = self._pw.chromium.launch_persistent_context(channel="chrome", **kw)
        except Exception:  # noqa: BLE001
            self._ctx = self._pw.chromium.launch_persistent_context(**kw)
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self._ctx.set_default_timeout(9000)
        return self

    def __exit__(self, *exc) -> None:
        pass

    def close(self) -> None:
        try:
            if self._ctx:
                self._ctx.close()
        finally:
            if self._pw:
                self._pw.stop()

    def _shot(self, name: str) -> None:
        try:
            self.page.screenshot(path=os.path.join(paths.data_dir(), f"grab_{name}.png"))
        except Exception:  # noqa: BLE001
            pass

    def ensure_login(self, wait_manual: bool = True, timeout_s: int = 300) -> bool:
        """로그인 화면을 띄우고, 극장별 예매로 넘어갈 때까지(=로그인 완료) 대기."""
        p = self.page
        p.goto(LOGIN_URL, wait_until="domcontentloaded")
        p.wait_for_timeout(1500)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if "movieBook" in p.url and "login" not in p.url:
                return True
            if not wait_manual:
                break
            p.wait_for_timeout(1000)
        return "movieBook" in p.url and "login" not in p.url

    def replay(self, recipe: dict, day: str, hhmm: str, movie: str = "",
               persons: dict | None = None, preferred: list[str] | None = None,
               only_preferred: bool = False, prefer: str = "center") -> tuple[bool, str, str]:
        persons = {k: int(v) for k, v in (persons or {"일반": 2}).items() if int(v) > 0}
        total = sum(persons.values()) or 1
        preferred = [s.strip().upper() for s in (preferred or [])]
        p = self.page
        try:
            day_num = str(int(day.split("-")[-1]))
        except Exception:  # noqa: BLE001
            day_num = day

        done = {"date": False, "showtime": False, "person": False, "seat": False}
        seat_str = ""
        try:
            for i, step in enumerate(recipe.get("steps", [])):
                kind = _classify(step)
                txt = (step.get("txt") or "").strip()
                # 날짜/회차/인원/좌석은 여러 번 기록됐어도 한 번만 처리
                if kind in done and done[kind]:
                    continue
                logger.info("[replay] %02d %s '%s'", i + 1, kind, txt[:16])

                if kind == "date":
                    d = p.locator("[class*='dayScroll_scrollItem']", has_text=day_num)
                    if d.count():
                        d.first.click()
                    p.wait_for_timeout(2500)
                    done["date"] = True

                elif kind == "showtime":
                    done["showtime"] = True
                    r = p.evaluate(MARK_SHOW_JS, {"hhmm": hhmm, "movie": movie})
                    if r == "nf":
                        # 아코디언 펼치고 재시도
                        try:
                            p.evaluate(
                                "(m)=>{const t=[...document.querySelectorAll(\"[class*='accordionTitle']\")]"
                                ".find(e=>e.textContent.includes(m)); if(t)t.click();}", movie)
                        except Exception:  # noqa: BLE001
                            pass
                        p.wait_for_timeout(1000)
                        r = p.evaluate(MARK_SHOW_JS, {"hhmm": hhmm, "movie": movie})
                    logger.info("[replay] 회차 표식: %s", r)
                    if not str(r).startswith("ok"):
                        self._shot("showtime"); return False, "", f"회차 {hhmm} 못 찾음"
                    p.locator("[data-ap='1']").first.click(timeout=7000)
                    p.wait_for_timeout(3000)
                    if "login" in p.url:
                        self._shot("login"); return False, "", "로그인 필요"

                elif kind == "person":
                    if not done["person"]:
                        self._pick_persons(persons)
                        done["person"] = True
                    # 이후 중복 person 스텝은 건너뜀

                elif kind == "seat":
                    if not done["seat"]:
                        ok, seat_str, msg = self._pick_seats(total, prefer, preferred, only_preferred)
                        if not ok:
                            return False, "", msg
                        done["seat"] = True

                else:  # literal — 기록된 텍스트 그대로 클릭
                    if not txt:
                        continue
                    r = p.evaluate(CLICK_TEXT_JS, {"txt": txt, "cls": step.get("cls", "")})
                    logger.info("[replay] 클릭 '%s' → %s", txt[:14], r)
                    if txt == "결제하기":
                        p.wait_for_timeout(2500)
                        self._shot("payment")
                        logger.info("[replay] 결제 페이지 진입 — 좌석 선점")
                        return True, seat_str, "좌석 선점 완료(결제 페이지). 카드 결제만 직접 하세요."
                p.wait_for_timeout(1000)

            # 결제하기 스텝이 없었으면 여기까지 = 선택완료 상태
            self._shot("done")
            return True, seat_str, "좌석 선택 완료(결제하기까지 녹화에 없었음)."
        except Exception as e:  # noqa: BLE001
            self._shot("error")
            logger.error("[replay] 실패: %s", e)
            return False, "", f"재생 오류: {e}"

    # ---- 하위 동작 ----
    def _pick_persons(self, persons: dict) -> None:
        p = self.page
        pick_js = r"""(a)=>{const {label,n}=a;
          const rows=[...document.querySelectorAll('*')].filter(e=>{
            const t=e.textContent||''; return t.includes(label)&&e.querySelector('button.btn-num');});
          rows.sort((x,y)=>(x.textContent||'').length-(y.textContent||'').length);
          const row=rows[0]; if(!row) return 'no-row';
          const b=[...row.querySelectorAll('button.btn-num')].find(x=>(x.textContent||'').trim()===String(n));
          if(!b) return 'no-btn'; b.scrollIntoView({block:'center'}); b.click(); return 'ok';}"""
        for label, n in persons.items():
            r = p.evaluate(pick_js, {"label": label, "n": n})
            logger.info("[replay] 인원 %s %d명 → %s", label, n, r)
            p.wait_for_timeout(600)

    def _pick_seats(self, total: int, prefer: str, preferred: list[str],
                    only_preferred: bool) -> tuple[bool, str, str]:
        p = self.page
        seats = p.locator(SEL_SEAT_OK)
        sn = seats.count()
        logger.info("[replay] 빈 좌석 %d개 (필요 %d)", sn, total)
        if sn == 0:
            self._shot("seat"); return False, "", "빈 좌석 없음 — 계속 감시"

        def label(el):
            return (el.inner_text() or "").strip().upper()

        labels = [(label(seats.nth(i)), i) for i in range(sn)]
        avail = {lb for lb, _ in labels}
        chosen: list[int] = []
        if preferred:
            for lb, idx in labels:
                if lb in preferred:
                    chosen.append(idx)
            if only_preferred and len(chosen) < total:
                got = [lb for lb in preferred if lb in avail]
                self._shot("seat")
                return False, "", f"원하는 좌석 대기중(가능:{','.join(got) or '없음'}/필요 {total})"
        if len(chosen) < total:
            pool = [idx for _, idx in labels if idx not in chosen]
            if prefer == "back":
                pool = list(reversed(pool))
            elif prefer == "center":
                mid = len(pool) // 2
                pool = sorted(pool, key=lambda x: abs(pool.index(x) - mid))
            chosen.extend(pool[: total - len(chosen)])
        chosen = chosen[:total]

        picked = []
        for idx in chosen:
            el = seats.nth(idx)
            picked.append(label(el))
            el.click(); p.wait_for_timeout(400)
        return True, ", ".join([x for x in picked if x]) or f"{len(chosen)}석", "ok"
