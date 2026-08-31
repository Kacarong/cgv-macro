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

# 텍스트로 '보이는' 요소를 찾아 클릭 (정확일치 우선 → 부분일치, 버튼/링크 우선)
CLICK_TEXT_JS = r"""(a)=>{
  const norm=s=>(s||'').replace(/\s+/g,' ').trim();
  const target=norm(a.txt); const clsHint=(a.cls||'').split(' ')[0];
  if(!target) return 'empty';
  const vis=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&e.offsetParent!==null;};
  const all=[...document.querySelectorAll('button,a,li,span,div,strong,em')].filter(vis);
  let cands=all.filter(e=>norm(e.textContent)===target);      // 1) 정확일치
  if(!cands.length) cands=all.filter(e=>norm(e.textContent).includes(target)); // 2) 부분일치
  if(clsHint){const pf=cands.filter(e=>typeof e.className==='string'&&e.className.includes(clsHint));
    if(pf.length) cands=pf;}
  cands.sort((x,y)=>{
    const bx=(x.tagName==='BUTTON'||x.tagName==='A')?0:1, by=(y.tagName==='BUTTON'||y.tagName==='A')?0:1;
    if(bx!==by) return bx-by;
    return norm(x.textContent).length-norm(y.textContent).length;});
  const el=cands[0]; if(!el) return 'nf';
  el.scrollIntoView({block:'center'}); el.click();
  return 'ok:'+norm(el.textContent).slice(0,18);
}"""

# 화면에서 '결제하기'가 들어간, 보이는 버튼 중 '가장 아래(모달의 빨간 버튼)'를 클릭
CLICK_PAY_JS = r"""()=>{
  const btns=[...document.querySelectorAll('button,a,div')].filter(e=>{
    const t=(e.textContent||'').replace(/\s+/g,'');
    if(!t.includes('결제하기')) return false;
    const r=e.getBoundingClientRect();
    return r.width>60 && r.height>10 && e.offsetParent!==null;});
  if(!btns.length) return 'nf';
  btns.sort((a,b)=>b.getBoundingClientRect().top-a.getBoundingClientRect().top);
  const el=btns[0]; el.scrollIntoView({block:'center'}); el.click();
  return 'ok@'+Math.round(el.getBoundingClientRect().top);
}"""

MARK_SHOW_JS = r"""(a)=>{const {hhmm,movie}=a;
  const T=[...document.querySelectorAll("[class*='accordionTitle']")];
  const pt=e=>{let b=null;for(const t of T){
    if(t.compareDocumentPosition(e)&Node.DOCUMENT_POSITION_FOLLOWING)b=t;}return b;};
  for(const sel of ["[class*='screenInfo_timeLink']","[class*='cinemaSchedule_scrollItemBtn']","[class*='screenInfo_timeWrap']"]){
    for(const lk of [...document.querySelectorAll(sel)]){const x=lk.textContent||"";
      if(x.includes(hhmm)&&!x.includes('예매종료')&&!x.includes('준비')){
        const t=pt(lk);
        if(!movie||(t&&t.textContent.includes(movie))){lk.setAttribute('data-ap','1');
          return 'ok:'+x.replace(/\s+/g,' ').slice(0,26);}}}
  }
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

    def _wait_visitor(self, timeout_ms: int) -> bool:
        """회차 클릭 후 인원/좌석 화면(selectVisitorCnt)으로 넘어갔는지 확인."""
        p = self.page
        waited = 0
        while waited < timeout_ms:
            if "selectVisitorCnt" in p.url:
                return True
            if p.locator("button.btn-num").count() > 0 or p.locator(SEL_SEAT_OK).count() > 0:
                return True
            p.wait_for_timeout(400)
            waited += 400
        return False

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
                        try:
                            d.first.scroll_into_view_if_needed(timeout=2000)
                        except Exception:  # noqa: BLE001
                            pass
                        d.first.click()
                    else:
                        logger.info("[replay] 날짜 %s일 버튼 못 찾음", day_num)
                    p.wait_for_timeout(2500)
                    done["date"] = True

                elif kind == "showtime":
                    done["showtime"] = True
                    r = p.evaluate(MARK_SHOW_JS, {"hhmm": hhmm, "movie": movie})
                    if r == "nf":
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
                    loc = p.locator("[data-ap='1']").first
                    try:
                        loc.scroll_into_view_if_needed(timeout=2000)
                    except Exception:  # noqa: BLE001
                        pass
                    loc.click(timeout=7000)
                    # 예매(인원) 화면으로 실제 전환됐는지 확인 — 안 넘어가면 좌표 실클릭 재시도
                    if not self._wait_visitor(4000):
                        try:
                            box = loc.bounding_box()
                            if box:
                                p.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                        except Exception:  # noqa: BLE001
                            pass
                        if not self._wait_visitor(6000):
                            self._shot("showtime")
                            return False, "", "회차 클릭했으나 예매(인원)로 전환 안 됨 — 로그인/대기열 확인"
                    logger.info("[replay] 예매(인원) 진입 OK")

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
                    if "결제하기" in txt:
                        if done.get("pay"):
                            continue
                        done["pay"] = True
                        # selectVisitorCnt 결제하기 → '결제 전 확인' 모달 결제하기 → 결제수단 페이지.
                        # 최대 2번만 클릭(그 이상 최종 결제 버튼은 절대 안 누름).
                        for i in range(2):
                            p.wait_for_timeout(1500)
                            if (p.locator("text=결제수단").count() or p.locator("text=간편결제").count()
                                    or p.locator("text=신용/체크카드").count()
                                    or "/payment" in p.url.lower()):
                                logger.info("[replay] 결제수단 페이지 감지 — 중단")
                                break
                            rr = p.evaluate(CLICK_PAY_JS)
                            logger.info("[replay] 결제 진행 클릭 %d: %s", i + 1, rr)
                            if not str(rr).startswith("ok"):
                                break
                        self._shot("payment")
                        logger.info("[replay] 결제(수단) 페이지 진입 — 좌석 선점 완료")
                        return True, seat_str, "좌석 선점 완료(결제 페이지). 카드 결제만 직접 하세요."
                    r = p.evaluate(CLICK_TEXT_JS, {"txt": txt, "cls": step.get("cls", "")})
                    logger.info("[replay] 클릭 '%s' → %s", txt[:14], r)
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
            # modal-bg 오버레이가 막아도 눌리도록 강제/직접 클릭
            try:
                el.click(force=True, timeout=4000)
            except Exception:  # noqa: BLE001
                try:
                    el.evaluate("e=>e.click()")
                except Exception:  # noqa: BLE001
                    pass
            p.wait_for_timeout(400)
        return True, ", ".join([x for x in picked if x]) or f"{len(chosen)}석", "ok"
