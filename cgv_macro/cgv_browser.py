"""
CGV 좌석 자동 클릭 (로그인된 실제 크롬 구동).

learn_seat 캡처(2026-08)로 확인한 실제 예매 흐름/셀렉터로 구현.
흐름: /cnm/movieBook/movie → 영화선택 → 극장선택(지역→지점→극장선택)
      → 날짜 → 회차(시간) → 인원(일반 N) → 좌석 클릭 → 선택완료(홀드).
※ 결제(0원 결제하기)는 절대 누르지 않는다 — 좌석 홀드까지만.

로그인은 chrome-profile(고정 프로필)에 저장된 세션을 재사용한다
(learn_seat.py 로 1회 로그인해두면 그대로 사용).
"""
from __future__ import annotations

import logging
import os
import time

from playwright.sync_api import sync_playwright

from . import paths

logger = logging.getLogger("cgv_macro")

BOOK_URL = "https://cgv.co.kr/cnm/movieBook/movie"
PROFILE = os.path.join(paths.data_dir(), "chrome-profile")

# ---- 확정 셀렉터(클래스 해시 뒷부분은 배포마다 바뀌므로 앞부분만 사용) ----
SEL_MOVIE_SEARCH = "input[placeholder*='영화명']"
SEL_SCHEDULE_BTN = "[class*='cinemaSchedule_scrollItemBtn']"
SEL_DAY_ITEM = "[class*='dayScroll_scrollItem']"
SEL_PERSON_NUM = "button.btn-num"            # 첫 8개=일반, 다음 8개=청소년
SEL_SEAT_AVAILABLE = "button[class*='seatMap_seatNumber']:not([class*='seatDisabled'])"
SEL_CONFIRM = "button:has-text('선택완료')"
SEL_THEATER_CONFIRM = "button:has-text('극장선택')"


class Booker:
    def __init__(self, headless: bool = False) -> None:
        self.headless = headless
        self._pw = None
        self._ctx = None
        self.page = None

    def __enter__(self) -> "Booker":
        self._pw = sync_playwright().start()
        kw = dict(user_data_dir=PROFILE, headless=self.headless, locale="ko-KR",
                  viewport={"width": 1440, "height": 960}, args=["--start-maximized"])
        try:
            self._ctx = self._pw.chromium.launch_persistent_context(channel="chrome", **kw)
        except Exception:  # noqa: BLE001
            self._ctx = self._pw.chromium.launch_persistent_context(**kw)
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self._ctx.set_default_timeout(8000)
        return self

    def __exit__(self, *exc) -> None:
        # 홀드 유지를 위해 컨텍스트를 바로 닫지 않는다(호출측에서 관리)
        pass

    def close(self) -> None:
        try:
            if self._ctx:
                self._ctx.close()
        finally:
            if self._pw:
                self._pw.stop()

    def _click_visible(self, selectors: list[str], timeout: int = 4000) -> bool:
        """여러 셀렉터 후보 중 '실제로 보이는' 첫 요소를 클릭."""
        for sel in selectors:
            try:
                loc = self.page.locator(sel)
                cnt = loc.count()
            except Exception:  # noqa: BLE001
                continue
            for i in range(min(cnt, 20)):
                el = loc.nth(i)
                try:
                    if el.is_visible():
                        el.scroll_into_view_if_needed(timeout=2000)
                        el.click(timeout=timeout)
                        return True
                except Exception:  # noqa: BLE001
                    continue
        return False

    def _shot(self, name: str) -> None:
        try:
            self.page.screenshot(path=os.path.join(paths.data_dir(), f"grab_{name}.png"))
        except Exception:  # noqa: BLE001
            pass

    def is_logged_in(self) -> bool:
        try:
            self.page.goto("https://cgv.co.kr/", wait_until="domcontentloaded")
            self.page.wait_for_timeout(1500)
            return self.page.locator("text=로그아웃").count() > 0
        except Exception:  # noqa: BLE001
            return False

    def grab(self, movie: str, theater: str, day: str, hhmm: str,
             count: int = 2, prefer: str = "center", preferred: list[str] | None = None,
             screen_type: str = "", region: str = "") -> tuple[bool, str, str]:
        """
        day: 'YYYY-MM-DD' 의 일(day) 숫자만 사용(예 '19'). hhmm: '21:10'.
        반환: (성공, 좌석문자열, 메시지)
        """
        preferred = [p.upper() for p in (preferred or [])]
        p = self.page
        try:
            day_num = str(int(day.split("-")[-1]))
        except Exception:  # noqa: BLE001
            day_num = day

        try:
            logger.info("[grab] cinema-flow v9 — %s / %s / %s일 / %s (지역 %s)",
                        movie, theater, day_num, hhmm, region or "?")
            # 극장별 예매 전용 페이지(팝업 없이 지역·극장·날짜·회차가 보이는 버튼)
            p.goto("https://cgv.co.kr/cnm/movieBook/cinema", wait_until="domcontentloaded")
            p.wait_for_timeout(3000)

            logger.info("[grab] region/theater v15")
            p.wait_for_timeout(1500)

            def _modal_blocking() -> bool:
                m = p.locator(".cgv-bot-modal.active, .cgv-modal.active")
                for i in range(min(m.count(), 5)):
                    try:
                        if m.nth(i).is_visible():
                            return True
                    except Exception:  # noqa: BLE001
                        pass
                return False

            # 1) 검색창에 극장명 입력(결과 필터 + 지역 자동선택)
            si = p.locator("input[placeholder*='극장'], input[placeholder*='지역']")
            if si.count():
                try:
                    si.first.click(); si.first.fill(theater); p.wait_for_timeout(1500)
                except Exception:  # noqa: BLE001
                    pass
            elif region:
                self._click_visible([f"text={region}"]); p.wait_for_timeout(1500)

            # 2) 결과에서 극장명과 '정확히 일치하는 보이는 요소'를 JS로 클릭
            click_theater_js = r"""(t)=>{
              const els=[...document.querySelectorAll('*')].filter(e=>
                e.children.length===0 && e.textContent && e.textContent.trim()===t);
              const vis=els.find(e=>{const r=e.getBoundingClientRect();
                return r.width>0 && r.height>0 && e.offsetParent!==null;});
              if(!vis) return 'no-theater('+els.length+')';
              vis.scrollIntoView({block:'center'}); vis.click(); return 'ok';
            }"""
            rt = p.evaluate(click_theater_js, theater)
            logger.info("[grab] 극장결과 클릭: %s", rt)
            if rt != "ok":
                # 폴백: 텍스트 보이는 것 클릭
                if not self._click_visible([f"text={theater}"]):
                    self._shot("2theater"); return False, "", f"극장 '{theater}' 결과 못 찾음"
            p.wait_for_timeout(900)

            # 3) '극장선택' 확정 버튼을 JS로 찾아 클릭 → 모달 닫힘 대기
            click_confirm_js = r"""()=>{
              const els=[...document.querySelectorAll('button,[role=button],a,div,span')].filter(e=>
                (e.textContent||'').trim()==='극장선택');
              const vis=els.find(e=>{const r=e.getBoundingClientRect();
                return r.width>0 && r.height>0 && e.offsetParent!==null;});
              if(!vis) return 'no-confirm('+els.length+')';
              vis.scrollIntoView({block:'center'}); vis.click(); return 'ok';
            }"""
            for i in range(8):
                if not _modal_blocking():
                    break
                cr = p.evaluate(click_confirm_js)
                if i == 0:
                    logger.info("[grab] 극장선택 확정: %s", cr)
                p.wait_for_timeout(900)
            logger.info("[grab] 극장모달 닫힘=%s", not _modal_blocking())
            p.wait_for_timeout(1200)
            self._shot("2theater")

            # 3) 날짜 선택 (dayScroll)
            logger.info("[grab] 날짜 선택: %s일", day_num)
            days = p.locator(SEL_DAY_ITEM, has_text=day_num)
            if days.count():
                days.first.click(); p.wait_for_timeout(2500)
            self._shot("3date")

            # 4) 회차 선택 — 영화 아코디언(movie) 아래 hhmm 회차를 표식 후 클릭
            logger.info("[grab] 회차 선택: %s (%s)", hhmm, movie)
            mark_js = r"""(a)=>{const {hhmm,movie}=a;
              const T=[...document.querySelectorAll("[class*='accordionTitle']")];
              const L=[...document.querySelectorAll("[class*='screenInfo_timeLink']")];
              const pt=e=>{let b=null;for(const t of T){
                  if(t.compareDocumentPosition(e)&Node.DOCUMENT_POSITION_FOLLOWING)b=t;}return b;};
              for(const lk of L){const x=lk.textContent||"";
                if(x.includes(hhmm)&&!x.includes('예매종료')&&!x.includes('준비')){
                  const t=pt(lk);
                  if(t&&t.textContent.includes(movie)){lk.setAttribute('data-ap','1');
                    return 'ok:'+x.replace(/\s/g,' ').slice(0,26);}}}
              return 'nf';}"""
            expand_js = r"""(m)=>{const ts=[...document.querySelectorAll("[class*='accordionTitle']")];
              const t=ts.find(e=>e.textContent.includes(m));if(t){t.click();return 'ok';}return 'no';}"""
            res = p.evaluate(mark_js, {"hhmm": hhmm, "movie": movie})
            if res == "nf":
                p.evaluate(expand_js, movie); p.wait_for_timeout(1200)
                res = p.evaluate(mark_js, {"hhmm": hhmm, "movie": movie})
            logger.info("[grab] 회차 표식: %s", res)
            if not str(res).startswith("ok"):
                self._shot("4schedule"); return False, "", f"회차 {hhmm}({movie}) 못 찾음(매진/미오픈?)"
            p.locator("[data-ap='1']").first.click(timeout=6000)
            p.wait_for_timeout(3000)
            self._shot("4schedule")

            # (로그인 안됐으면 로그인 페이지로 감)
            if "login" in p.url:
                self._shot("login"); return False, "", "로그인 필요 — learn_seat.py 로 먼저 로그인하세요"

            # 5) 인원 선택 — 일반 count 명(첫 그룹의 count번째)
            logger.info("[grab] 인원(일반 %d)", count)
            nums = p.locator(SEL_PERSON_NUM)
            if nums.count() >= count:
                nums.nth(count - 1).click(); p.wait_for_timeout(2000)
            self._shot("4person")

            # 6) 좌석 선택
            seats = p.locator(SEL_SEAT_AVAILABLE)
            sn = seats.count()
            logger.info("[grab] 예매가능 좌석 %d개", sn)
            if sn == 0:
                self._shot("seat"); return False, "", "좌석 화면에 가능 좌석 없음"

            def label(el) -> str:
                return (el.inner_text() or "").strip().upper()

            labels = [(label(seats.nth(i)), i) for i in range(sn)]
            chosen: list[int] = []
            if preferred:
                for lb, idx in labels:
                    if lb in preferred:
                        chosen.append(idx)
            if len(chosen) < count:
                pool = [idx for _, idx in labels if idx not in chosen]
                if prefer == "back":
                    pool = list(reversed(pool))
                elif prefer == "center":
                    mid = len(pool) // 2
                    pool = sorted(pool, key=lambda x: abs(pool.index(x) - mid))
                chosen.extend(pool[: count - len(chosen)])
            chosen = chosen[:count]

            picked = []
            for idx in chosen:
                el = seats.nth(idx)
                picked.append(label(el))
                el.click(); p.wait_for_timeout(400)
            seat_str = ", ".join([x for x in picked if x]) or f"{len(chosen)}석"

            # 7) 선택완료(좌석 홀드) — 결제는 하지 않음
            conf = p.locator(SEL_CONFIRM)
            if conf.count():
                conf.first.click(); p.wait_for_timeout(1500)
            self._shot("done")
            logger.info("[grab] 좌석 홀드 완료: %s", seat_str)
            return True, seat_str, "좌석 홀드 완료(결제 전). 빨리 결제하세요."
        except Exception as e:  # noqa: BLE001
            self._shot("error")
            logger.error("[grab] 실패: %s", e)
            return False, "", f"자동 클릭 오류: {e}"
