"""
좌석 자동 잡기 '재생기' — 녹화(recipe)로 확정한 실제 예매 흐름/셀렉터를 사용.

흐름(2026-08 recipe 실측):
  /cnm/movieBook/cinema (극장 선택된 상태) → 날짜(dayScroll) → 회차(오디세이 hhmm)
  → /cnm/selectVisitorCnt → 인원(btn-num 일반 N) → '선택' → 좌석(seatMap 빈자리) → '선택완료'
  ※ '결제하기'는 절대 누르지 않는다(좌석 홀드까지만).

로그인은 chrome-profile(record.py 로 로그인한 것) 재사용.
"""
from __future__ import annotations

import logging
import os

from playwright.sync_api import sync_playwright

from . import paths

logger = logging.getLogger("cgv_macro")

CINEMA_URL = "https://cgv.co.kr/cnm/movieBook/cinema"
PROFILE = os.path.join(paths.data_dir(), "chrome-profile")

SEL_DAY = "[class*='dayScroll_scrollItem']"
SEL_SEAT_OK = "button[class*='seatMap_seatNumber']:not([class*='seatDisabled'])"


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
        pass  # 홀드 유지 위해 자동 종료 안 함

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

    def grab(self, movie: str, day: str, hhmm: str, count: int = 2,
             prefer: str = "center", preferred: list[str] | None = None) -> tuple[bool, str, str]:
        """day='2026-09-04'(일 숫자만 사용), hhmm='20:00'. 반환 (성공, 좌석문자열, 메시지)."""
        preferred = [s.strip().upper() for s in (preferred or [])]
        p = self.page
        try:
            day_num = str(int(day.split("-")[-1]))
        except Exception:  # noqa: BLE001
            day_num = day

        try:
            logger.info("[grab] v-recipe — %s / %s일 / %s", movie, day_num, hhmm)
            p.goto(CINEMA_URL, wait_until="domcontentloaded")
            p.wait_for_timeout(3000)

            # 극장 미선택이면(스케줄/날짜 안 뜸) 실패 안내 — 프로필에 극장이 기억돼 있어야 함
            if p.locator(SEL_DAY).count() == 0:
                self._shot("0cinema")
                return False, "", "극장(센텀시티)이 선택돼 있지 않습니다. 크롬에서 한 번 센텀시티를 골라두세요."

            # 1) 날짜
            logger.info("[grab] 날짜 %s일", day_num)
            days = p.locator(SEL_DAY, has_text=day_num)
            if days.count():
                days.first.click(); p.wait_for_timeout(2500)
            self._shot("1date")

            # 2) 회차 — 영화 아코디언(movie) 아래 hhmm 회차 표식 후 클릭
            mark_js = r"""(a)=>{const {hhmm,movie}=a;
              const T=[...document.querySelectorAll("[class*='accordionTitle']")];
              const L=[...document.querySelectorAll("[class*='screenInfo_timeLink'],[class*='screenInfo_timeWrap']")];
              const pt=e=>{let b=null;for(const t of T){
                if(t.compareDocumentPosition(e)&Node.DOCUMENT_POSITION_FOLLOWING)b=t;}return b;};
              for(const lk of L){const x=lk.textContent||"";
                if(x.includes(hhmm)&&!x.includes('예매종료')&&!x.includes('준비')){
                  const t=pt(lk);
                  if(!movie || (t&&t.textContent.includes(movie))){
                    lk.setAttribute('data-ap','1'); return 'ok:'+x.replace(/\s+/g,' ').slice(0,26);}}}
              return 'nf';}"""
            expand_js = r"""(m)=>{const ts=[...document.querySelectorAll("[class*='accordionTitle']")];
              const t=ts.find(e=>e.textContent.includes(m));if(t){t.click();return 'ok';}return 'no';}"""
            res = p.evaluate(mark_js, {"hhmm": hhmm, "movie": movie})
            if res == "nf":
                p.evaluate(expand_js, movie); p.wait_for_timeout(1200)
                res = p.evaluate(mark_js, {"hhmm": hhmm, "movie": movie})
            logger.info("[grab] 회차 표식: %s", res)
            if not str(res).startswith("ok"):
                self._shot("2schedule"); return False, "", f"회차 {hhmm}({movie}) 못 찾음"
            p.locator("[data-ap='1']").first.click(timeout=7000)
            p.wait_for_timeout(3000)
            self._shot("2schedule")

            if "login" in p.url:
                self._shot("login")
                return False, "", "로그인 필요 — record.py 로 로그인해두세요"

            # 3) 인원 — 일반 count 명(첫 그룹 count번째)
            logger.info("[grab] 인원 일반 %d", count)
            nums = p.locator("button.btn-num")
            if nums.count() >= count:
                nums.nth(count - 1).click(); p.wait_for_timeout(1200)

            # 4) '선택'(좌석으로 진행) — '선택완료'와 구분 위해 정확 일치
            try:
                p.get_by_role("button", name="선택", exact=True).first.click(timeout=5000)
            except Exception:  # noqa: BLE001
                p.locator("[class*='cnms01520_btnBgAnimation']").first.click()
            p.wait_for_timeout(2000)
            self._shot("3person")

            # 5) 좌석 — 빈자리 count개 선택(가운데 우선 / 선호좌석 우선)
            seats = p.locator(SEL_SEAT_OK)
            sn = seats.count()
            logger.info("[grab] 빈 좌석 %d개", sn)
            if sn == 0:
                self._shot("4seat"); return False, "", "빈 좌석 없음"

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

            # 6) 선택완료(홀드) — 결제는 안 함
            try:
                p.get_by_role("button", name="선택완료").first.click(timeout=5000)
            except Exception:  # noqa: BLE001
                p.locator("button:has-text('선택완료')").first.click()
            p.wait_for_timeout(1500)
            self._shot("5done")
            logger.info("[grab] 좌석 홀드 완료: %s", seat_str)
            return True, seat_str, "좌석 홀드 완료(결제 전). 빨리 결제하세요."
        except Exception as e:  # noqa: BLE001
            self._shot("error")
            logger.error("[grab] 실패: %s", e)
            return False, "", f"자동 클릭 오류: {e}"
