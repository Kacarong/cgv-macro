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
            logger.info("[grab] 예매 페이지 이동")
            p.goto(BOOK_URL, wait_until="domcontentloaded")
            p.wait_for_timeout(2500)

            # 1) 영화 선택 — 보이는 포스터/버튼만 클릭(검색 입력은 숨은 중복 유발하므로 생략)
            logger.info("[grab] 영화 선택: %s", movie)
            if not self._click_visible([f"img[alt='{movie}']", f"img[alt*='{movie}']",
                                        f"button:has-text('{movie}')", f"text={movie}"]):
                self._shot("movie"); return False, "", f"영화 '{movie}' 선택 실패(포스터 안 보임)"
            p.wait_for_timeout(2000)

            self._shot("1movie")
            # 2) 극장 선택 — '선택 된 극장이 없습니다' 옆의 +(극장 추가)만 정확히 클릭
            #    (상단 X 닫기 버튼과 혼동 금지). 모달이 실제 열렸는지 검증.
            logger.info("[grab] 극장 선택: %s (지역 %s)", theater, region or "?")

            logger.info("[grab] theater-open v5")

            def _modal_open() -> bool:
                return (p.locator("input[placeholder*='지역']").count() > 0
                        or p.locator("text=지역별").count() > 0)

            # '선택 된 극장이 없습니다' 줄의 오른쪽(⊕ 위치)을 훑어 클릭 가능한 요소를 클릭
            open_js = r"""() => {
              const all=[...document.querySelectorAll('*')];
              const label=all.find(e=>e.children.length===0 && e.textContent &&
                    e.textContent.trim().includes('선택 된 극장'));
              if(!label) return 'no-label';
              const lr=label.getBoundingClientRect();
              const y=lr.top+lr.height/2;
              const xStart=Math.min(window.innerWidth-30, lr.left+640);
              for(let x=xStart; x>lr.right; x-=5){
                const el=document.elementFromPoint(x,y);
                if(!el) continue;
                let n=el;
                for(let k=0;k<5 && n;k++,n=n.parentElement){
                  const cs=getComputedStyle(n);
                  if(n.tagName==='BUTTON'||n.getAttribute('role')==='button'||cs.cursor==='pointer'){
                    n.click();
                    return 'clicked@'+Math.round(x)+','+Math.round(y)+' <'+n.tagName+'>';
                  }
                }
              }
              return 'no-plus';
            }"""
            for attempt in range(2):
                if _modal_open():
                    break
                try:
                    res = p.evaluate(open_js)
                except Exception as e:  # noqa: BLE001
                    res = f"err:{e}"
                logger.info("[grab] 극장추가 스캔클릭#%d: %s", attempt + 1, res)
                p.wait_for_timeout(1800)
            self._shot("2a_modalopen")
            logger.info("[grab] 모달열림=%s 지역'%s'후보=%d 지점'%s'후보=%d",
                        _modal_open(), region, p.locator(f"text={region}").count(),
                        theater, p.locator(f"text={theater}").count())
            if not _modal_open():
                self._shot("2theater"); return False, "", "극장 추가(+) 모달 열기 실패"

            # 지역 먼저 선택(모달 왼쪽)
            if region:
                self._click_visible([f"text={region}", f":has-text('{region}')"])
                p.wait_for_timeout(1500)
            self._shot("2b_region")
            logger.info("[grab] 지역선택 후 지점'%s' 후보=%d", theater,
                        p.locator(f"text={theater}").count())
            # 지점 선택
            if not self._click_visible([f"text={theater}"]):
                self._shot("2theater")
                return False, "", f"극장 '{theater}' 선택 실패(지역 모달 확인 필요)"
            p.wait_for_timeout(700)
            self._shot("2c_site")
            # 극장선택 확정
            self._click_visible(["text=극장선택", "button:has-text('극장선택')",
                                 ":has-text('극장선택')"])
            for _ in range(10):
                if p.locator(".cgv-bot-modal.active, .modal-bg").count() == 0:
                    break
                p.wait_for_timeout(500)
            p.wait_for_timeout(1000)
            self._shot("2theater")

            # 3) 날짜 선택
            logger.info("[grab] 날짜 선택: %s일", day_num)
            days = p.locator(SEL_DAY_ITEM, has_text=day_num)
            if days.count():
                days.first.click(); p.wait_for_timeout(1500)

            # 4) 회차(시간) 선택 — 예매종료/준비중 제외, 시작시각 매칭
            logger.info("[grab] 회차 선택: %s", hhmm)
            shows = p.locator(SEL_SCHEDULE_BTN)
            n = shows.count()
            target_btn = None
            for i in range(n):
                el = shows.nth(i)
                txt = (el.inner_text() or "")
                if hhmm in txt and "예매종료" not in txt and "준비" not in txt:
                    if screen_type and screen_type.lower() not in txt.lower():
                        continue
                    target_btn = el; break
            if target_btn is None:
                self._shot("3schedule"); return False, "", f"회차 {hhmm} 을(를) 못 찾음(매진/미오픈?)"
            try:
                target_btn.scroll_into_view_if_needed(timeout=2000)
            except Exception:  # noqa: BLE001
                pass
            target_btn.click(); p.wait_for_timeout(2500)
            self._shot("3schedule")

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
