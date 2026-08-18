"""
CGV 접근 어댑터 (Playwright).

핵심 아이디어:
 - 실제 브라우저(로그인 세션 유지)로 예매 페이지를 열면, 페이지가 스스로
   api.cgv.co.kr 로 스케줄/좌석 JSON 을 요청한다. 우리는 그 응답을 가로채(capture)
   잔여석을 읽는다. DOM 만 긁는 것보다 구조 변화에 강하다.
 - 좌석 자동 선택이 필요할 때만 좌석 페이지에서 DOM 클릭으로 좌석을 잡는다.

CGV 구조 의존값은 전부 selectors.py 에 있다. 여기 로직은 그 값들을 소비한다.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from . import selectors as S

logger = logging.getLogger("cgv_macro")


def run_login(browser_cfg: dict, confirm_event, result: dict) -> None:
    """
    GUI 에서 호출: headed 브라우저로 CGV 로그인 페이지를 열고, 사용자가
    로그인 후 confirm_event 를 set 할 때까지 기다렸다가 세션을 저장/종료한다.
    반드시 자체 스레드에서 호출해야 한다(Playwright sync API 스레드 규칙).
    """
    try:
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=browser_cfg.get("user_data_dir", "./session"),
                headless=False,
                locale="ko-KR",
                viewport={"width": 1366, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                ),
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(S.LOGIN_URL)
            # 사용자가 '로그인 완료' 를 누를 때까지 대기(최대 10분)
            confirm_event.wait(timeout=600)
            ok = False
            try:
                page.goto(S.SITE_BASE, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
                ok = page.locator(S.LOGGED_IN_HINT_SELECTOR).count() > 0
            except Exception:  # noqa: BLE001
                pass
            ctx.close()
            result["ok"] = ok
    except Exception as e:  # noqa: BLE001
        result["error"] = str(e)


@dataclass
class Showtime:
    time: str                 # "19:20"
    screen: str = ""          # 상영관명
    remaining: int = -1       # 잔여석 수(-1=미상)
    total: int = -1
    soldout: bool = False
    schedule_id: str = ""
    booking_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def showtime_key(self) -> str:
        return f"{self.time}|{self.screen}"


# ----------------------- JSON 파싱 헬퍼 -----------------------
def _first_field(d: dict[str, Any], candidates: list[str]) -> Any:
    for k in candidates:
        if k in d and d[k] not in (None, ""):
            return d[k]
    # 대소문자 무시 재시도
    lower = {str(k).lower(): v for k, v in d.items()}
    for k in candidates:
        if k.lower() in lower and lower[k.lower()] not in (None, ""):
            return lower[k.lower()]
    return None


def _to_int(v: Any) -> int:
    try:
        return int(str(v).strip())
    except Exception:  # noqa: BLE001
        return -1


def _norm_time(v: Any) -> str:
    """'1920' / '192000' / '19:20' → '19:20'."""
    s = re.sub(r"[^0-9:]", "", str(v))
    if ":" in s:
        parts = s.split(":")
        return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    digits = re.sub(r"\D", "", s)
    if len(digits) >= 4:
        return f"{digits[0:2]}:{digits[2:4]}"
    return str(v)


def _is_truthy_flag(v: Any) -> bool:
    return str(v).strip().lower() in ("y", "true", "1", "yes")


def _iter_dicts(obj: Any):
    """중첩 JSON 안의 모든 dict 를 순회."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _iter_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_dicts(v)


def _find_schedule_items(body: Any) -> list[dict[str, Any]]:
    """
    응답 JSON 에서 '회차 항목처럼 보이는 dict' 들을 찾는다.
    회차 항목 = 시간 필드와 (잔여석 or 매진) 필드를 함께 가진 dict.
    """
    items: list[dict[str, Any]] = []
    for d in _iter_dicts(body):
        has_time = _first_field(d, S.SHOWTIME_TIME_FIELDS) is not None
        has_seat = (
            _first_field(d, S.REMAINING_SEAT_FIELDS) is not None
            or _first_field(d, S.SOLDOUT_FIELDS) is not None
        )
        if has_time and has_seat:
            items.append(d)
    return items


def parse_showtimes(json_bodies: list[Any]) -> list[Showtime]:
    """캡처한 여러 JSON 응답에서 회차 목록을 뽑아 중복 제거."""
    seen: dict[str, Showtime] = {}
    for body in json_bodies:
        for d in _find_schedule_items(body):
            t = _norm_time(_first_field(d, S.SHOWTIME_TIME_FIELDS))
            remaining = _to_int(_first_field(d, S.REMAINING_SEAT_FIELDS))
            total = _to_int(_first_field(d, S.TOTAL_SEAT_FIELDS))
            soldout_flag = _first_field(d, S.SOLDOUT_FIELDS)
            soldout = _is_truthy_flag(soldout_flag) if soldout_flag is not None else (remaining == 0)
            screen = str(_first_field(d, S.SCREEN_NAME_FIELDS) or "")
            sid = str(_first_field(d, S.SCHEDULE_ID_FIELDS) or "")
            st = Showtime(
                time=t, screen=screen, remaining=remaining, total=total,
                soldout=soldout, schedule_id=sid, raw=d,
            )
            seen[st.showtime_key()] = st
    return sorted(seen.values(), key=lambda s: s.time)


def _in_time_range(hhmm: str, start: str, end: str) -> bool:
    def m(x: str) -> int:
        try:
            h, mm = x.split(":")
            return int(h) * 60 + int(mm)
        except Exception:  # noqa: BLE001
            return -1
    v = m(hhmm)
    return v >= 0 and m(start) <= v <= m(end)


# ----------------------- Playwright 클라이언트 -----------------------
class CgvClient:
    def __init__(self, browser_cfg: dict[str, Any]) -> None:
        self.cfg = browser_cfg
        self._pw = None
        self._ctx = None

    def __enter__(self) -> "CgvClient":
        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=self.cfg.get("user_data_dir", "./session"),
            headless=bool(self.cfg.get("headless", True)),
            slow_mo=int(self.cfg.get("slow_mo_ms", 0)),
            viewport={"width": 1366, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="ko-KR",
        )
        self._ctx.set_default_navigation_timeout(int(self.cfg.get("nav_timeout_ms", 30000)))
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self._ctx:
                self._ctx.close()
        finally:
            if self._pw:
                self._pw.stop()

    def is_logged_in(self) -> bool:
        page = self._ctx.new_page()
        try:
            page.goto(S.SITE_BASE, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            return page.locator(S.LOGGED_IN_HINT_SELECTOR).count() > 0
        except Exception as e:  # noqa: BLE001
            logger.debug("로그인 상태 확인 실패: %s", e)
            return False
        finally:
            page.close()

    def fetch_showtimes(self, target) -> tuple[list[Showtime], str]:
        """
        대상 예매 페이지로 이동하며 api.cgv.co.kr 스케줄 응답을 캡처 → 회차 목록 반환.
        반환: (회차목록, 사용한 booking_url)
        """
        page = self._ctx.new_page()
        captured: list[Any] = []

        def on_response(resp):
            try:
                url = resp.url
                # 인증 API(api.cgv.co.kr) + 같은도메인 프록시(/api/v1/booking) 응답 모두 캡처
                if (S.API_HOST in url or S.API_PROXY in url) and \
                        "json" in (resp.headers.get("content-type", "")):
                    captured.append(resp.json())
            except Exception:  # noqa: BLE001
                pass

        page.on("response", on_response)

        booking_url = target.booking_url or S.BOOKING_ENTRY_URL
        try:
            page.goto(booking_url, wait_until="domcontentloaded")
            # 페이지가 booking_url 만으로 회차를 못 불러오면, UI 탐색을 시도한다.
            if not target.booking_url:
                self._navigate_booking_ui(page, target)
            page.wait_for_timeout(3500)  # XHR 완료 대기
        except PWTimeout:
            logger.warning("[%s] 페이지 로드 타임아웃", target.name)
        except Exception as e:  # noqa: BLE001
            logger.warning("[%s] 페이지 탐색 실패: %s", target.name, e)
        finally:
            current_url = page.url
            page.close()

        showtimes = parse_showtimes(captured)
        # 시간대 필터 + 상영관 필터
        filtered = [
            s for s in showtimes
            if _in_time_range(s.time, target.time_from, target.time_to)
            and (not target.screen_type or target.screen_type.lower() in s.screen.lower())
        ]
        for s in filtered:
            s.booking_url = current_url
        logger.debug("[%s] 캡처 응답 %d개 / 회차 %d개 / 필터후 %d개",
                     target.name, len(captured), len(showtimes), len(filtered))
        return filtered, current_url

    def _navigate_booking_ui(self, page, target) -> None:
        """
        booking_url 이 없을 때 예매 UI 를 클릭으로 진행하는 '베스트에포트' 경로.
        ⚠ CGV 개편에 따라 실제 클릭 순서/셀렉터가 다를 수 있음.
        가장 확실한 방법은 config 에 target.booking_url 을 직접 넣는 것(README 참고).
        여기서는 검색창에 영화명을 넣는 정도만 시도한다.
        """
        try:
            movie_q = target.movie or target.movie_code
            if not movie_q:
                return
            # 검색 UI 후보 — 사이트 변경 시 조정 필요
            search = page.locator(
                "input[type='search'], input[placeholder*='검색'], input[name*='search']"
            )
            if search.count() > 0:
                search.first.click()
                search.first.fill(movie_q)
                page.keyboard.press("Enter")
                page.wait_for_timeout(2500)
        except Exception as e:  # noqa: BLE001
            logger.debug("[%s] 예매 UI 탐색 생략: %s", target.name, e)

    def auto_select_seats(
        self, showtime: Showtime, auto_cfg: dict[str, Any]
    ) -> tuple[bool, str]:
        """
        좌석 페이지에서 좌석을 골라 홀드(다음 버튼)까지 진행. 결제는 하지 않음.
        반환: (성공여부, 좌석정보문자열)
        ⚠ 좌석 페이지 진입 경로는 사이트 구조에 의존. schedule_id/booking_url 이 필요.
        """
        if not showtime.booking_url:
            return False, ""
        count = int(auto_cfg.get("count", 2))
        preferred = [p.upper() for p in (auto_cfg.get("preferred_seats") or [])]
        prefer = str(auto_cfg.get("prefer", "center")).lower()

        page = self._ctx.new_page()
        try:
            page.goto(showtime.booking_url, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)

            seats = page.locator(S.SEAT_AVAILABLE_SELECTOR)
            n = seats.count()
            if n == 0:
                logger.info("좌석 요소를 찾지 못함(셀렉터 확인 필요).")
                return False, ""

            def label_of(el) -> str:
                for attr in S.SEAT_LABEL_ATTRS:
                    v = el.get_attribute(attr)
                    if v:
                        return v.strip().upper()
                return (el.inner_text() or "").strip().upper()

            # 좌석 목록 수집
            candidates = []
            for i in range(n):
                el = seats.nth(i)
                candidates.append((label_of(el), i))

            chosen: list[int] = []
            # 1) 선호 좌석 우선
            if preferred:
                for lbl, idx in candidates:
                    if lbl in preferred:
                        chosen.append(idx)
            # 2) prefer 위치 기반 보충
            if len(chosen) < count:
                pool = [idx for _, idx in candidates if idx not in chosen]
                if prefer == "front":
                    pool = pool  # DOM 순서상 앞쪽
                elif prefer == "back":
                    pool = list(reversed(pool))
                elif prefer == "center":
                    mid = len(pool) // 2
                    pool = sorted(pool, key=lambda x: abs(pool.index(x) - mid))
                chosen.extend(pool[: count - len(chosen)])

            chosen = chosen[:count]
            picked_labels = []
            for idx in chosen:
                el = seats.nth(idx)
                picked_labels.append(label_of(el))
                el.click()
                page.wait_for_timeout(300)

            # 좌석 홀드 확정(다음 버튼) — 결제 진입 전 단계까지만
            confirm = page.locator(S.SEAT_CONFIRM_SELECTOR)
            if confirm.count() > 0:
                confirm.first.click()
                page.wait_for_timeout(1500)

            seat_info = ", ".join([l for l in picked_labels if l]) or f"{len(chosen)}석 선택"
            logger.info("좌석 선택 완료: %s", seat_info)
            # 홀드 상태 유지를 위해 페이지를 닫지 않는 편이 안전할 수 있으나,
            # 세션 컨텍스트가 살아있으므로 페이지는 유지한다(닫지 않음).
            return True, seat_info
        except Exception as e:  # noqa: BLE001
            logger.error("좌석 자동 선택 실패: %s", e)
            return False, ""
        # NOTE: page 를 일부러 닫지 않음 — 홀드 유지 목적
