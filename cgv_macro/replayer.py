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

# 화면에서 '결제하기'가 든 보이는 버튼 중 '가장 아래' 것을 data-pay 로 표식(실제 클릭은 Playwright)
MARK_PAY_JS = r"""()=>{
  const btns=[...document.querySelectorAll('button,a,div')].filter(e=>{
    const t=(e.textContent||'').replace(/\s+/g,'');
    if(!t.includes('결제하기')) return false;
    const r=e.getBoundingClientRect();
    return r.width>60 && r.height>10 && e.offsetParent!==null;});
  if(!btns.length) return null;
  btns.sort((a,b)=>b.getBoundingClientRect().top-a.getBoundingClientRect().top);
  document.querySelectorAll('[data-pay]').forEach(e=>e.removeAttribute('data-pay'));
  btns[0].setAttribute('data-pay','1');
  return Math.round(btns[0].getBoundingClientRect().top);
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
        m = re.findall(r"\d+", day or "")
        day_num = str(int(m[-1])) if m else ""
        logger.info("[replay] 대상 날짜=%s(일=%s) 시간=%s 영화=%s",
                    day, day_num or "(없음)", hhmm, movie)
        if not day_num:
            return False, "", "날짜가 비어있습니다. 날짜를 YYYY-MM-DD 로 입력하세요."

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
                    done["date"] = True
                    # 보이는 날짜 항목 중 '숫자가 정확히 그 날'인 것을 표식 → 실제 클릭
                    mark_date_js = r"""(dd)=>{
                      const items=[...document.querySelectorAll("[class*='dayScroll_scrollItem']")].filter(e=>{
                        const r=e.getBoundingClientRect(); return r.width>0&&r.height>0&&e.offsetParent!==null;});
                      const dig=s=>(s||'').replace(/[^0-9]/g,'');
                      document.querySelectorAll('[data-day]').forEach(e=>e.removeAttribute('data-day'));
                      const el=items.find(e=>parseInt(dig(e.textContent),10)===parseInt(dd,10));
                      if(!el) return null; el.setAttribute('data-day','1'); return dig(el.textContent);
                    }"""
                    got = p.evaluate(mark_date_js, day_num)
                    if got:
                        loc = p.locator("[data-day='1']").first
                        try:
                            loc.scroll_into_view_if_needed(timeout=2000)
                        except Exception:  # noqa: BLE001
                            pass
                        try:
                            loc.click(timeout=4000)
                        except Exception:  # noqa: BLE001
                            try:
                                loc.click(force=True, timeout=3000)
                            except Exception:  # noqa: BLE001
                                pass
                        logger.info("[replay] 날짜 클릭: %s일", got)
                    else:
                        logger.info("[replay] 날짜 %s일 못 찾음(달력에 안 보임?)", day_num)
                    p.wait_for_timeout(1500)

                elif kind == "showtime":
                    done["showtime"] = True
                    # 시간표가 늦게 뜰 수 있으니 회차가 나타날 때까지 최대 ~6초 폴링
                    r = "nf"
                    for attempt in range(20):
                        r = p.evaluate(MARK_SHOW_JS, {"hhmm": hhmm, "movie": movie})
                        if str(r).startswith("ok"):
                            break
                        if attempt == 4:  # 중간에 한 번 영화 아코디언 펼치기
                            try:
                                p.evaluate(
                                    "(m)=>{const t=[...document.querySelectorAll(\"[class*='accordionTitle']\")]"
                                    ".find(e=>e.textContent.includes(m)); if(t)t.click();}", movie)
                            except Exception:  # noqa: BLE001
                                pass
                        p.wait_for_timeout(300)
                    logger.info("[replay] 회차 표식: %s", r)
                    if not str(r).startswith("ok"):
                        self._shot("showtime")
                        return False, "", f"회차 {hhmm} 못 찾음 — 그 날 그 시간 회차가 실제로 있는지 확인하세요"
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
                        # 좌석요약 결제하기 → '결제 전 확인' 모달 결제하기 → 결제수단 페이지.
                        def _pay_page() -> bool:
                            return bool(p.locator("text=결제수단").count() or p.locator("text=간편결제").count()
                                        or p.locator("text=신용/체크카드").count()
                                        or p.locator("text=포인트/쿠폰").count()
                                        or "/payment" in p.url.lower())

                        def _confirm_open() -> bool:
                            return bool(p.locator("text=결제 전 확인").count())

                        def _click_pay(tag: str, tries: int) -> bool:
                            top = None
                            for _ in range(tries):
                                top = p.evaluate(MARK_PAY_JS)
                                if top is not None:
                                    break
                                p.wait_for_timeout(120)
                            if top is None:
                                return False
                            loc = p.locator("[data-pay='1']").first
                            try:
                                loc.scroll_into_view_if_needed(timeout=2000)
                            except Exception:  # noqa: BLE001
                                pass
                            try:
                                loc.click(timeout=3000)          # 정상 클릭(핸들러 확실히 발동)
                            except Exception:  # noqa: BLE001
                                try:
                                    loc.click(force=True, timeout=2000)
                                except Exception:  # noqa: BLE001
                                    pass
                            logger.info("[replay] 결제하기 %s @top%s", tag, top)
                            return True

                        # 1번: 좌석요약의 결제하기 → 확인 모달이 뜰 때까지 필요시 재시도
                        for attempt in range(3):
                            if _confirm_open() or _pay_page():
                                break
                            _click_pay(f"1.{attempt}", 40)
                            for _ in range(30):  # 최대 ~3.6s 모달 대기
                                if _confirm_open() or _pay_page():
                                    break
                                p.wait_for_timeout(120)
                        # 2번: '결제 전 확인' 모달의 결제하기를 '모달이 사라질 때까지' 재시도.
                        # (모달이 있을 때만 누르므로 결제수단 페이지 최종 결제버튼은 절대 안 눌림)
                        for attempt in range(4):
                            if _pay_page() or not _confirm_open():
                                break
                            p.wait_for_timeout(350)  # 모달 안정화
                            _click_pay(f"2.{attempt}", 15)
                            for _ in range(30):
                                if _pay_page() or not _confirm_open():
                                    break
                                p.wait_for_timeout(120)
                        self._shot("payment")
                        logger.info("[replay] 결제 진행 결과: 결제수단페이지=%s", _pay_page())
                        return True, seat_str, "좌석 선점 완료(결제 페이지). 카드 결제만 직접 하세요."
                    r = p.evaluate(CLICK_TEXT_JS, {"txt": txt, "cls": step.get("cls", "")})
                    logger.info("[replay] 클릭 '%s' → %s", txt[:14], r)
                p.wait_for_timeout(450)

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
            p.wait_for_timeout(300)

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
            p.wait_for_timeout(200)
        return True, ", ".join([x for x in picked if x]) or f"{len(chosen)}석", "ok"
