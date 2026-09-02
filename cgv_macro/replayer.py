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

# '결제하기' 버튼을 data-pay 로 표식(실제 클릭은 Playwright).
# '결제 전 확인' 모달이 열려 있으면 검색 범위를 그 모달로 한정 → 배경(좌석요약) 버튼 오클릭 방지.
MARK_PAY_JS = r"""()=>{
  const norm=s=>(s||'').replace(/\s+/g,'');
  const vis=e=>{const r=e.getBoundingClientRect();return r.width>60&&r.height>10&&e.offsetParent!==null;};
  // '결제 전 확인' + '결제하기' 를 동시에 품은 '가장 안쪽' 컨테이너를 모달로 간주
  let modal=null;
  const holders=[...document.querySelectorAll('div,section,article,form')].filter(e=>{
    const t=e.textContent||''; return t.includes('결제 전 확인')&&t.includes('결제하기');});
  if(holders.length){ holders.sort((a,b)=>(a.textContent||'').length-(b.textContent||'').length); modal=holders[0]; }
  const scope = modal || document;
  // 버튼 타입에 상관없이(모달의 결제하기가 div/span 일 수 있음) 넓게 후보 수집
  let cands=[...scope.querySelectorAll('button,a,div,span')].filter(e=>norm(e.textContent).includes('결제하기')&&vis(e));
  if(!cands.length) return null;
  // 화면상 '가장 아래' 것(하단 큰 결제 버튼) 선택
  cands.sort((a,b)=>b.getBoundingClientRect().top-a.getBoundingClientRect().top);
  document.querySelectorAll('[data-pay]').forEach(e=>e.removeAttribute('data-pay'));
  cands[0].setAttribute('data-pay','1');
  return Math.round(cands[0].getBoundingClientRect().top);
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


# '결제 가능 시간 안내'(연장하시겠습니까?) 팝업의 '확인'을 눌러 좌석 선점 시간을 연장.
# 연장 안내 모달 안에서만 '확인'을 찾아 클릭(다른 확인 버튼 오클릭 방지). 1=클릭함.
EXTEND_CONFIRM_JS = r"""()=>{
  const norm=s=>(s||'').replace(/\s+/g,'');
  const vis=e=>{const r=e.getBoundingClientRect();return r.width>10&&r.height>8&&e.offsetParent!==null;};
  const isOk=e=>{const t=norm(e.textContent);return (t==='확인'||t==='연장'||t==='연장하기')&&vis(e);};
  // '연장' 안내 텍스트 + '확인' 버튼을 '함께' 품은 컨테이너만 모달로 인정(본문만 있는 조각 제외)
  const holders=[...document.querySelectorAll('div,section,article,dialog')].filter(e=>{
    const t=e.textContent||'';
    if(!(t.includes('연장하시겠')||(t.includes('결제 가능 시간')&&t.includes('연장')))) return false;
    return [...e.querySelectorAll('button,a,div,span')].some(isOk);});
  if(!holders.length) return 0;
  holders.sort((a,b)=>(a.textContent||'').length-(b.textContent||'').length);   // 가장 안쪽(=모달)
  const modal=holders[0];
  const btns=[...modal.querySelectorAll('button,a,div,span')].filter(isOk);
  if(!btns.length) return 0;
  // '취소'는 제외됨. 확인 버튼 클릭(가장 아래=주버튼).
  btns.sort((a,b)=>b.getBoundingClientRect().top-a.getBoundingClientRect().top);
  btns[0].click();
  return 1;
}"""


def _label_in_region(lb: str, region: dict) -> bool:
    """좌석 라벨('C35')이 지정 구역(열/번호 범위) 안인지."""
    m = re.match(r"^([A-Z]{1,2})(\d+)$", (lb or "").strip().upper())
    if not m:
        return False
    r, num = m.group(1), int(m.group(2))
    rf = (region.get("row_from") or "").strip().upper()
    rt = (region.get("row_to") or "").strip().upper()
    nf, nt = region.get("num_from"), region.get("num_to")
    if rf and r < rf:
        return False
    if rt and r > rt:
        return False
    if nf is not None and num < nf:
        return False
    if nt is not None and num > nt:
        return False
    return True


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


def clone_profile(idx: int) -> str:
    """마스터 로그인 프로필(PROFILE)을 대상별 폴더로 복제(로그인 세션 공유).
    각 대상이 '독립된 크롬 창'을 갖도록 profile 폴더를 분리한다. 캐시/락 파일은 제외."""
    import shutil
    dst = os.path.join(paths.data_dir(), f"chrome-profile-{idx}")
    shutil.rmtree(dst, ignore_errors=True)
    if os.path.isdir(PROFILE):
        ignore = shutil.ignore_patterns(
            "Singleton*", "Cache", "Code Cache", "GPUCache", "ShaderCache",
            "GrShaderCache", "DawnCache", "Crashpad", "*.log", "*-journal")
        try:
            shutil.copytree(PROFILE, dst, ignore=ignore, dirs_exist_ok=True)
        except Exception:  # noqa: BLE001
            os.makedirs(dst, exist_ok=True)
    else:
        os.makedirs(dst, exist_ok=True)
    for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            os.remove(os.path.join(dst, lock))
        except OSError:
            pass
    return dst


class Grabber:
    def __init__(self, headless: bool = False, profile_dir: str | None = None,
                 storage_state: str | None = None, win_pos: tuple[int, int] | None = None,
                 shot_prefix: str = "") -> None:
        self.headless = headless
        self.profile = profile_dir or PROFILE
        self.storage_state = storage_state   # 지정 시: 저장된 로그인 세션으로 독립 창(병렬용)
        self.win_pos = win_pos
        self.shot_prefix = shot_prefix       # 스크린샷 파일 접두(창별 충돌 방지)
        self._pw = None
        self._browser = None
        self._ctx = None
        self.page = None

    def __enter__(self) -> "Grabber":
        self._pw = sync_playwright().start()
        if self.storage_state is not None:
            # 독립 브라우저 + 저장된 세션 주입 → 창마다 별도 프로세스로 '진짜 병렬' + 로그인 공유
            pos = self.win_pos or (40, 40)
            args = [f"--window-position={pos[0]},{pos[1]}", "--window-size=1180,880"]
            try:
                self._browser = self._pw.chromium.launch(channel="chrome", headless=self.headless, args=args)
            except Exception:  # noqa: BLE001
                self._browser = self._pw.chromium.launch(headless=self.headless, args=args)
            ss = self.storage_state if os.path.exists(self.storage_state) else None
            self._ctx = self._browser.new_context(storage_state=ss, locale="ko-KR",
                                                  viewport={"width": 1160, "height": 820})
            self.page = self._ctx.new_page()
        else:
            kw = dict(user_data_dir=self.profile, headless=self.headless, locale="ko-KR",
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

    def click_extend_confirm(self, page) -> bool:
        """'결제 가능 시간 연장' 팝업이 있으면 '확인'을 눌러 좌석 선점을 연장. True=클릭함."""
        try:
            return bool(page.evaluate(EXTEND_CONFIRM_JS))
        except Exception:  # noqa: BLE001
            return False

    def export_state(self, path: str) -> None:
        """현재 로그인 세션(쿠키/스토리지)을 파일로 저장 → 다른 창에 주입해 로그인 공유."""
        try:
            self._ctx.storage_state(path=path)
        except Exception:  # noqa: BLE001
            pass

    def new_page(self):
        """동시 선점용 새 탭. 같은 컨텍스트라 로그인 세션을 공유한다."""
        pg = self._ctx.new_page()
        pg.set_default_timeout(9000)
        return pg

    def new_window(self, idx: int = 0):
        """별도 '창'(팝업)을 연다. 같은 컨텍스트라 로그인 세션을 공유하면서도
        탭이 아닌 독립 창으로 떠서 여러 개를 한눈에 볼 수 있다. 팝업 차단 시 탭으로 폴백."""
        left = 30 + (idx % 4) * 90 + (idx // 4) * 20
        top = 30 + (idx % 4) * 70
        feats = f"popup=yes,width=1180,height=860,left={left},top={top}"
        try:
            with self._ctx.expect_page(timeout=8000) as info:
                self.page.evaluate("(f)=>window.open('about:blank','_blank',f)", feats)
            pg = info.value
        except Exception:  # noqa: BLE001
            pg = self._ctx.new_page()   # 팝업이 막히면 탭으로라도 진행
        try:
            pg.set_default_timeout(9000)
        except Exception:  # noqa: BLE001
            pass
        return pg

    def close(self) -> None:
        try:
            if self._ctx:
                self._ctx.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._browser:
                self._browser.close()
        except Exception:  # noqa: BLE001
            pass
        if self._pw:
            self._pw.stop()

    def _shot(self, page, name: str) -> None:
        try:
            page.screenshot(path=os.path.join(paths.data_dir(), f"grab_{self.shot_prefix}{name}.png"))
        except Exception:  # noqa: BLE001
            pass

    def _wait_visitor(self, page, timeout_ms: int) -> bool:
        """회차 클릭 후 인원/좌석 화면(selectVisitorCnt)으로 넘어갔는지 확인."""
        p = page
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

    def wait_login_passive(self, timeout_s: int = 600) -> bool:
        """현재 페이지(로그인 화면)를 '다시 이동시키지 않고' 로그인 완료만 기다린다.
        사용자가 입력 중인 폼이 초기화되지 않도록 goto/reload 를 하지 않는다."""
        p = self.page
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                if "movieBook" in p.url and "login" not in p.url:
                    return True
            except Exception:  # noqa: BLE001
                pass
            try:
                p.wait_for_timeout(1000)
            except Exception:  # noqa: BLE001
                time.sleep(1)
        try:
            return "movieBook" in (p.url or "") and "login" not in (p.url or "")
        except Exception:  # noqa: BLE001
            return False

    def quick_login_check(self, timeout_ms: int = 7000) -> bool:
        """로그인 화면으로 잠깐 이동해 redirect 여부로 로그인 상태 판단. True=로그인 유지.
        로그인돼 있으면 CGV가 즉시 극장별예매로 리다이렉트하고, 아니면 로그인 화면에 머문다."""
        p = self.page
        try:
            p.goto(LOGIN_URL, wait_until="domcontentloaded")
        except Exception:  # noqa: BLE001
            return True  # 판단 불가 → 오탐 방지 위해 로그인된 것으로 간주
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            if "movieBook" in p.url and "login" not in p.url:
                return True
            p.wait_for_timeout(400)
        return False

    def replay(self, recipe: dict, day: str, hhmm: str, movie: str = "",
               persons: dict | None = None, preferred: list[str] | None = None,
               only_preferred: bool = False, prefer: str = "center",
               page=None, log=None, deadline_s: int = 120,
               region: dict | None = None) -> tuple[bool, str, str]:
        persons = {k: int(v) for k, v in (persons or {"일반": 2}).items() if int(v) > 0}
        total = sum(persons.values()) or 1
        preferred = [s.strip().upper() for s in (preferred or [])]
        p = page or self.page
        deadline = time.time() + deadline_s

        def _lg(msg):   # 진행 상황을 앱 로그로도 실시간 표시
            logger.info("[replay] %s", msg)
            if log:
                try:
                    log(msg)
                except Exception:  # noqa: BLE001
                    pass

        m = re.findall(r"\d+", day or "")
        day_num = str(int(m[-1])) if m else ""
        _lg(f"예매 진입 시작 (날짜 {day_num}일 / 회차 {hhmm} / 좌석 {total}석)")
        if not day_num:
            return False, "", "날짜가 비어있습니다. 날짜를 YYYY-MM-DD 로 입력하세요."

        done = {"date": False, "showtime": False, "person": False, "seat": False}
        seat_str = ""
        try:
            # 매 시도 예매 페이지를 '새로' 불러온다 → 자정 넘어도 달력이 최신(날짜 어긋남 방지).
            try:
                p.goto(CINEMA_URL, wait_until="domcontentloaded")
                p.wait_for_timeout(1500)
            except Exception:  # noqa: BLE001
                pass
            for i, step in enumerate(recipe.get("steps", [])):
                if time.time() > deadline:
                    self._shot(p, "timeout")
                    return False, "", "시간초과 — 이번엔 못 잡음(다음 감지 때 재시도)"
                kind = _classify(step)
                txt = (step.get("txt") or "").strip()
                # 날짜/회차/인원/좌석은 여러 번 기록됐어도 한 번만 처리
                if kind in done and done[kind]:
                    continue
                logger.info("[replay] %02d %s '%s'", i + 1, kind, txt[:16])

                if kind == "date":
                    done["date"] = True
                    # 날짜 '숫자만' 든 요소(리프) 중 정확히 그 날을 표식 → 실제 클릭
                    mark_date_js = r"""(dd)=>{
                      const target=parseInt(dd,10);
                      const vis=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&e.offsetParent!==null;};
                      document.querySelectorAll('[data-day]').forEach(e=>e.removeAttribute('data-day'));
                      let cands=[...document.querySelectorAll("[class*='dayScroll_number'],[class*='dayScroll_scrollItem'] span,[class*='dayScroll_scrollItem']")]
                        .filter(e=>{const t=(e.textContent||'').replace(/[^0-9]/g,'');return t!==''&&parseInt(t,10)===target&&vis(e);});
                      if(!cands.length) return null;
                      cands.sort((a,b)=>(a.textContent||'').replace(/\s+/g,'').length-(b.textContent||'').replace(/\s+/g,'').length);
                      cands[0].setAttribute('data-day','1');
                      return (cands[0].textContent||'').replace(/[^0-9]/g,'');
                    }"""
                    verify_date_js = r"""()=>{
                      const all=[...document.querySelectorAll("[class*='dayScroll']")];
                      const sel=all.find(e=>{const c=((e.className||'')+'');const t=(e.textContent||'').replace(/[^0-9]/g,'');
                        return t!==''&&(c.includes('c-red')||c.includes('active')||c.includes('selected')||e.getAttribute('aria-selected')==='true');});
                      return sel?(sel.textContent||'').replace(/[^0-9]/g,''):null;
                    }"""
                    # 달력이 아직 안 그려졌을 수 있으니 뜰 때까지 최대 ~8초 폴링
                    got = None
                    for _ in range(27):
                        got = p.evaluate(mark_date_js, day_num)
                        if got:
                            break
                        p.wait_for_timeout(300)
                    if got:
                        loc = p.locator("[data-day='1']").first
                        try:
                            loc.scroll_into_view_if_needed(timeout=2000)
                        except Exception:  # noqa: BLE001
                            pass
                        # 실제 마우스 좌표 클릭(정확한 날짜 요소를 확실히 선택)
                        clicked = False
                        try:
                            box = loc.bounding_box()
                            if box and box["height"] > 0:
                                p.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                                clicked = True
                        except Exception:  # noqa: BLE001
                            pass
                        if not clicked:
                            for how in ("normal", "force", "js"):
                                try:
                                    if how == "normal":
                                        loc.click(timeout=3000)
                                    elif how == "force":
                                        loc.click(force=True, timeout=2000)
                                    else:
                                        loc.evaluate("e=>e.click()")
                                    break
                                except Exception:  # noqa: BLE001
                                    continue
                        p.wait_for_timeout(1200)
                        sel = None
                        try:
                            sel = p.evaluate(verify_date_js)
                        except Exception:  # noqa: BLE001
                            pass
                        if sel and str(int(sel)) != day_num:
                            _lg(f"⚠️ 날짜 {day_num}일 클릭했는데 {int(sel)}일이 선택됨 — 재시도")
                            # 목표 숫자 재표식 후 다시 클릭
                            try:
                                p.evaluate(mark_date_js, day_num)
                                loc2 = p.locator("[data-day='1']").first
                                box2 = loc2.bounding_box()
                                if box2:
                                    p.mouse.click(box2["x"] + box2["width"] / 2, box2["y"] + box2["height"] / 2)
                                p.wait_for_timeout(1000)
                                sel = p.evaluate(verify_date_js)
                            except Exception:  # noqa: BLE001
                                pass
                        _lg(f"날짜 {day_num}일 선택 (달력 표시: {int(sel) if sel else '?'}일)")
                    else:
                        _lg(f"날짜 {day_num}일 못 찾음(달력 로딩 지연?)")
                    p.wait_for_timeout(1300)

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
                    if not str(r).startswith("ok"):
                        self._shot(p, "showtime")
                        return False, "", f"회차 {hhmm} 못 찾음 — 그 날 그 시간 회차가 실제로 있는지 확인하세요"
                    _lg(f"회차 {hhmm} 선택 → 예매 진입")
                    loc = p.locator("[data-ap='1']").first
                    try:
                        loc.scroll_into_view_if_needed(timeout=2000)
                    except Exception:  # noqa: BLE001
                        pass
                    loc.click(timeout=7000)
                    # 예매(인원) 화면으로 실제 전환됐는지 확인 — 안 넘어가면 좌표 실클릭 재시도
                    if not self._wait_visitor(p, 4000):
                        try:
                            box = loc.bounding_box()
                            if box:
                                p.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                        except Exception:  # noqa: BLE001
                            pass
                        if not self._wait_visitor(p, 6000):
                            self._shot(p, "showtime")
                            return False, "", "회차 클릭했으나 예매(인원)로 전환 안 됨 — 로그인/대기열 확인"
                    _lg("인원/좌석 화면 진입")

                elif kind == "person":
                    if not done["person"]:
                        self._pick_persons(p, persons)
                        done["person"] = True
                        _lg("인원 선택 완료")
                    # 이후 중복 person 스텝은 건너뜀

                elif kind == "seat":
                    if not done["seat"]:
                        ok, seat_str, msg = self._pick_seats(p, total, prefer, preferred, only_preferred, region)
                        if not ok:
                            _lg(f"좌석 대기: {msg}")
                            return False, "", msg
                        done["seat"] = True
                        _lg(f"좌석 선택: {seat_str} → 결제 진행")

                else:  # literal — 기록된 텍스트 그대로 클릭
                    if not txt:
                        continue
                    if "결제하기" in txt:
                        if done.get("pay"):
                            continue
                        done["pay"] = True
                        _lg("결제하기 클릭 진행…")
                        reached = self._do_payment(p, deadline=deadline, log=_lg)
                        _lg("결제 페이지 도달 ✓" if reached else "결제 페이지 미도달(수동 결제하기 필요)")
                        if reached:
                            return True, seat_str, "좌석 선점 완료(결제 페이지). 카드 결제만 직접 하세요."
                        return True, seat_str, "좌석 선택됨 — '결제하기'가 안 눌려 결제페이지 미도달. 창에서 직접 결제하기를 눌러 확인하세요"
                    r = p.evaluate(CLICK_TEXT_JS, {"txt": txt, "cls": step.get("cls", "")})
                    logger.info("[replay] 클릭 '%s' → %s", txt[:14], r)
                p.wait_for_timeout(450)

            # 결제하기 스텝이 없었으면 여기까지 = 선택완료 상태
            self._shot(p, "done")
            return True, seat_str, "좌석 선택 완료(결제하기까지 녹화에 없었음)."
        except Exception as e:  # noqa: BLE001
            self._shot(p, "error")
            logger.error("[replay] 실패: %s", e)
            return False, "", f"재생 오류: {e}"

    # ---- 하위 동작 ----
    def _pick_persons(self, page, persons: dict) -> None:
        p = page
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

    def _do_payment(self, page, deadline: float | None = None, log=None) -> bool:
        def _lg(m):
            if log:
                try:
                    log(m)
                except Exception:  # noqa: BLE001
                    pass
        """좌석 선택 후 '결제하기'(좌석요약) → '결제 전 확인' 모달 결제하기 → 결제 페이지 도달.
        끈질기게 재시도하고, 결제 페이지 도달을 넓게 판정한다. True=결제 페이지 도달.
        (결제수단 페이지의 '최종 결제'는 모달이 있을 때만 누르므로 절대 누르지 않는다.)"""
        p = page
        start_url = ""
        try:
            start_url = p.url or ""
        except Exception:  # noqa: BLE001
            pass

        def _left_seat() -> bool:
            try:
                u = p.url or ""
            except Exception:  # noqa: BLE001
                return False
            return ("selectVisitorCnt" not in u) and (u != start_url) and bool(u)

        def _pay_page() -> bool:
            try:
                u = (p.url or "").lower()
            except Exception:  # noqa: BLE001
                u = ""
            if any(k in u for k in ("/payment", "/order", "/pay", "paymethod")):
                return True
            for t in ("결제수단", "간편결제", "신용/체크카드", "포인트/쿠폰", "결제 정보", "최종 결제금액", "일반결제"):
                try:
                    if p.locator(f"text={t}").count():
                        return True
                except Exception:  # noqa: BLE001
                    pass
            return False

        def _confirm_open() -> bool:
            try:
                return bool(p.locator("text=결제 전 확인").count())
            except Exception:  # noqa: BLE001
                return False

        def _click_pay(tries: int) -> bool:
            top = None
            for _ in range(tries):
                try:
                    top = p.evaluate(MARK_PAY_JS)
                except Exception:  # noqa: BLE001
                    top = None
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
            # 1) 실제 '마우스 좌표' 클릭 — React onClick 핸들러가 확실히 발동됨(가상클릭이 안 먹는 경우 대비)
            try:
                box = loc.bounding_box()
                if box and box["height"] > 0:
                    p.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    return True
            except Exception:  # noqa: BLE001
                pass
            # 2) 폴백: 일반/강제/JS 클릭
            for how in ("normal", "force", "js"):
                try:
                    if how == "normal":
                        loc.click(timeout=3000)
                    elif how == "force":
                        loc.click(force=True, timeout=2000)
                    else:
                        loc.evaluate("e=>e.click()")
                    return True
                except Exception:  # noqa: BLE001
                    continue
            return False

        def _clear_extend():
            # '결제 가능 시간 연장' 팝업이 결제하기를 가리면 먼저 치운다(막힘 방지)
            try:
                self.click_extend_confirm(p)
            except Exception:  # noqa: BLE001
                pass

        def _over():
            return deadline is not None and time.time() > deadline

        # 1) 좌석요약 '결제하기' → 확인 모달 또는 결제 페이지 (끈질기게)
        for _attempt in range(6):
            if _over():
                break
            _clear_extend()
            if _confirm_open() or _pay_page():
                break
            _lg(f"좌석요약 결제하기 클릭 시도 #{_attempt+1}")
            _click_pay(40)
            for _ in range(50):   # 최대 ~7.5s 대기
                _clear_extend()
                if _confirm_open() or _pay_page():
                    break
                p.wait_for_timeout(150)
        if _confirm_open():
            _lg("결제 전 확인 모달 열림")
        # 2) '결제 전 확인' 모달의 결제하기 → 결제 페이지 (모달 사라질 때까지, 로딩 넉넉히)
        for _attempt in range(8):
            if _over():
                break
            _clear_extend()
            if _pay_page() or not _confirm_open():
                break
            p.wait_for_timeout(400)
            _lg(f"확인 모달 결제하기 클릭 시도 #{_attempt+1}")
            _click_pay(20)
            for _ in range(70):   # 최대 ~10.5s 대기(결제수단 페이지 로딩 지연 대비)
                _clear_extend()
                if _pay_page() or _left_seat() or not _confirm_open():
                    break
                p.wait_for_timeout(150)
        self._shot(p, "payment")
        if _pay_page():
            return True
        # 좌석선택 화면을 벗어났고 확인모달도 없으면 결제 단계로 넘어간 것으로 간주
        return _left_seat() and not _confirm_open()

    def _on_seatmap(self, page) -> bool:
        try:
            if "selectVisitorCnt" not in (page.url or ""):
                return False
            return page.locator("button[class*='seatMap_seatNumber']").count() > 0
        except Exception:  # noqa: BLE001
            return False

    def seatmap_recheck(self, page, need: int, preferred: list[str], only_preferred: bool,
                        prefer: str, region: dict | None = None) -> tuple[bool, str, str]:
        """좌석표에 머무른 채 새로고침해 좌석만 빠르게 재확인(취소표 효율화, 2번).
        좌석표가 아니면 'NEED_FULL' 반환 → 호출부에서 전체 재생으로 폴백."""
        try:
            if not self._on_seatmap(page):
                return False, "", "NEED_FULL"
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            if not self._on_seatmap(page):
                return False, "", "NEED_FULL"
            ok, seat_str, msg = self._pick_seats(page, need, prefer,
                                                 [s.strip().upper() for s in (preferred or [])],
                                                 only_preferred, region)
            if not ok:
                return False, "", msg   # '원하는 좌석 대기중' 등 — 좌석표 유지
            self._do_payment(page)
            return True, seat_str, "좌석 선점 완료(결제 페이지, 빠른재확인)."
        except Exception as e:  # noqa: BLE001
            return False, "", "NEED_FULL"

    @staticmethod
    def _auto_pick(remaining: list, need: int, prefer: str, region: dict | None = None) -> list:
        """자동 좌석 선택: 지정 구역(열/번호 범위) 안에서 같은 열 '붙어있는 need석' 우선
        + 열 우선순위(center/front/back). remaining: [(label, idx)]. 라벨은 'A16'(열문자+번호)."""
        region = region or {}
        rf = (region.get("row_from") or "").strip().upper()
        rt = (region.get("row_to") or "").strip().upper()
        nf = region.get("num_from")
        nt = region.get("num_to")

        def in_region(r, num):
            if rf and r < rf:
                return False
            if rt and r > rt:
                return False
            if nf is not None and num < nf:
                return False
            if nt is not None and num > nt:
                return False
            return True

        parsed = []
        for lb, idx in remaining:
            m = re.match(r"^([A-Z]{1,2})(\d+)$", lb)
            if m and in_region(m.group(1), int(m.group(2))):
                parsed.append((m.group(1), int(m.group(2)), idx))
        rows = sorted({r for r, _, _ in parsed})

        def row_rank(r):
            if not rows:
                return 0
            i = rows.index(r); n = len(rows)
            if prefer == "front":
                return i
            if prefer == "back":
                return n - 1 - i
            if prefer == "any":
                return 0
            return abs(i - (n - 1) / 2)   # center

        # 1) 붙어있는 need석(같은 열, 연속 번호) 블록 우선
        if need >= 2 and parsed:
            by_row: dict = {}
            for r, num, idx in parsed:
                by_row.setdefault(r, []).append((num, idx))
            best = None
            for r, sl in by_row.items():
                sl.sort()
                for k in range(len(sl) - need + 1):
                    win = sl[k:k + need]
                    if win[-1][0] - win[0][0] == need - 1:   # 연속 번호
                        rowmid = (sl[0][0] + sl[-1][0]) / 2
                        blockmid = (win[0][0] + win[-1][0]) / 2
                        score = (row_rank(r), abs(blockmid - rowmid))
                        if best is None or score < best[0]:
                            best = (score, [i for _, i in win])
            if best:
                return best[1]

        # 2) 붙어있는 블록이 없으면 열 우선순위대로 개별 선택
        if parsed:
            parsed.sort(key=lambda t: (row_rank(t[0]), t[1]))
            picked = [idx for _, _, idx in parsed[:need]]
        else:
            picked = []
        # 3) 라벨 파싱 안 되는 좌석까지 포함해 부족분 채움(단, 구역 지정이 없을 때만)
        region_set = bool(rf or rt or nf is not None or nt is not None)
        if len(picked) < need and not region_set:
            for _lb, idx in remaining:
                if idx not in picked:
                    picked.append(idx)
                if len(picked) >= need:
                    break
        return picked[:need]

    @staticmethod
    def _seat_labels(p) -> list:
        seats = p.locator(SEL_SEAT_OK)
        out = []
        for i in range(seats.count()):
            try:
                lb = (seats.nth(i).inner_text() or "").strip().upper()
            except Exception:  # noqa: BLE001
                lb = ""
            if lb:
                out.append(lb)
        return out

    def _click_seat_by_label(self, p, lb: str) -> bool:
        """좌석표에서 라벨이 정확히 lb인 빈 좌석을 '지금' 찾아 클릭(위치 index 재사용 금지).
        화면 밖(가로 스크롤 필요) 좌석도 Playwright 정상 클릭이 자동 스크롤해서 확실히 누른다."""
        seats = p.locator(SEL_SEAT_OK)
        for i in range(seats.count()):
            el = seats.nth(i)
            try:
                if (el.inner_text() or "").strip().upper() != lb:
                    continue
            except Exception:  # noqa: BLE001
                continue
            try:
                el.scroll_into_view_if_needed(timeout=2500)
            except Exception:  # noqa: BLE001
                pass
            # 1) Playwright 정상 클릭 — 좌석까지 자동 스크롤 + actionability 보장(가장 신뢰)
            try:
                el.click(timeout=4000)
                return True
            except Exception:  # noqa: BLE001
                pass
            # 2) 강제 클릭(오버레이가 막을 때)
            try:
                el.click(force=True, timeout=2500)
                return True
            except Exception:  # noqa: BLE001
                pass
            # 3) 좌표 클릭(스크롤 후 뷰포트 안일 때만)
            try:
                box = el.bounding_box()
                if box and box["height"] > 0 and box["y"] >= 0 and box["x"] >= 0:
                    p.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    return True
            except Exception:  # noqa: BLE001
                pass
            # 4) JS 클릭
            try:
                el.evaluate("e=>e.click()")
                return True
            except Exception:  # noqa: BLE001
                return False
        return False

    def _pick_seats(self, page, total: int, prefer: str, preferred: list[str],
                    only_preferred: bool, region: dict | None = None) -> tuple[bool, str, str]:
        p = page
        region = region or {}
        region_set = bool((region.get("row_from") or region.get("row_to")
                           or region.get("num_from") is not None or region.get("num_to") is not None))
        labels = self._seat_labels(p)
        logger.info("[replay] 빈 좌석 %d개 (필요 %d)", len(labels), total)
        if not labels:
            self._shot(p, "seat"); return False, "", "빈 좌석 없음 — 계속 감시"
        avail = set(labels)

        # 목표 좌석 '라벨' 결정
        targets: list[str] = []
        if preferred:
            for lb in preferred:
                if lb in avail and lb not in targets:
                    targets.append(lb)
            if only_preferred and len(targets) < total:
                self._shot(p, "seat")
                return False, "", f"원하는 좌석 대기중(가능:{','.join(targets) or '없음'}/필요 {total})"
        if len(targets) < total:
            remaining = [(lb, i) for i, lb in enumerate(labels) if lb not in targets]
            for idx in self._auto_pick(remaining, total - len(targets), prefer, region):
                if 0 <= idx < len(labels) and labels[idx] not in targets:
                    targets.append(labels[idx])
        # 구역을 지정했는데 그 구역에 앉을 자리가 부족하면 → 잡지 않고 대기(구역 밖 좌석 방지)
        if region_set and len(targets) < total:
            self._shot(p, "seat")
            return False, "", f"원하는 구역 대기중(현재 {len(targets)}/{total}석 · 구역 밖은 안 잡음)"
        # 중복 라벨 제거
        seen = set(); uniq = []
        for lb in targets:
            if lb not in seen:
                seen.add(lb); uniq.append(lb)
        targets = uniq[:total]

        # 라벨별로 '매번 새로 찾아' 클릭(선택하면 좌석표가 바뀌므로 index 재사용 금지)
        picked: list[str] = []
        for lb in targets:
            if self._click_seat_by_label(p, lb):
                picked.append(lb)
                p.wait_for_timeout(250)
            elif not only_preferred:
                # 그 좌석이 사라졌으면 다른 빈자리로 대체(구역 지정 시 구역 안에서만)
                for alt in self._seat_labels(p):
                    if alt in picked:
                        continue
                    if region_set and not _label_in_region(alt, region):
                        continue
                    if self._click_seat_by_label(p, alt):
                        picked.append(alt); p.wait_for_timeout(250); break
        picked = [x for x in dict.fromkeys(picked) if x]   # 중복/빈값 제거
        if not picked:
            self._shot(p, "seat")
            return False, "", "좌석 클릭 실패 — 계속 감시"

        # 실제 '선택됨' 검증(선택 좌석은 클래스에 select 표시). 감지되면 부족분 재클릭.
        def _sel_labels():
            try:
                return p.evaluate(r"""()=>{const out=[];
                  for(const b of document.querySelectorAll("button[class*='seatMap_seatNumber']")){
                    const c=((b.className||'')+'').toLowerCase();
                    const ap=((b.getAttribute('aria-pressed')||b.getAttribute('aria-selected')||'')+'').toLowerCase();
                    if(c.includes('select')||ap==='true') out.push((b.textContent||'').trim().toUpperCase());}
                  return out;}""") or []
            except Exception:  # noqa: BLE001
                return []
        sel = _sel_labels()
        if sel:   # 선택 마커 감지 가능한 경우에만 엄격 검증(감지 불가 시 기존 동작 유지)
            if len(sel) < total:
                for lb in list(dict.fromkeys(picked + targets)):
                    if lb not in sel and self._click_seat_by_label(p, lb):
                        p.wait_for_timeout(250)
                sel = _sel_labels()
            if len(sel) < total:
                self._shot(p, "seat")
                return False, "", f"좌석 선택 확인 실패({len(sel)}/{total}석) — 재시도"
            picked = sel[:total]
        return True, ", ".join(picked), "ok"
