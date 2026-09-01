"""
모든 감시(취소표·상영오픈·무대인사)가 공유하는 단일 브라우저 허브.

핵심: Playwright 동기 API는 '브라우저를 만든 스레드'에서만 조작할 수 있다.
그래서 브라우저 전용 스레드 1개를 두고, 로그인/좌석잡기/로그인점검 등 모든
브라우저 작업을 큐로 그 스레드에 넘겨 실행한다(자연히 하나씩 직렬 실행).
감시 스레드들은 결과만 기다린다 → 'cannot switch to a different thread' 해결.

- 크롬(persistent profile)은 1개만. 로그인 1회 → 프로필 세션 유지(자리 비워도 자동).
- 좌석잡기는 잡을 때마다 '새 탭'에서 진행 → 여러 개 동시 선점(각 결제창 유지).
- held 는 사용자 중지/종료 신호(모든 감시 정지).
"""
from __future__ import annotations

import logging
import queue
import threading

logger = logging.getLogger("cgv_macro")


class WatchHub:
    def __init__(self) -> None:
        self._q: "queue.Queue" = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="cgv-browser", daemon=True)
        self._thread.start()
        self._grabber = None
        self._closed = False

        self.held = threading.Event()       # 사용자 중지/종료 신호
        self.logged_in = False
        self._login_lost_notified = False

        self._holds_lock = threading.Lock()
        self.holds = 0                      # 현재 선점(결제창 대기)한 탭 수
        self.max_holds = 5
        self._pages: dict = {}              # 대상키 -> 창(닫지 않고 재사용 → 껐다켰다 방지)
        self._slots: dict = {}              # 대상키 -> 창 위치 슬롯
        self._next_slot = 0

    # ---- 브라우저 전용 스레드 ----
    def _loop(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                break
            fn, box = item
            try:
                box["result"] = fn()
            except Exception as e:  # noqa: BLE001
                box["error"] = e
            finally:
                box["done"].set()

    def _submit(self, fn):
        """브라우저 스레드에서 fn 실행하고 결과 반환(블로킹)."""
        if self._closed:
            raise RuntimeError("hub closed")
        box: dict = {"done": threading.Event()}
        self._q.put((fn, box))
        box["done"].wait()
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def _ensure_grabber(self):
        # 반드시 브라우저 스레드 안에서 호출됨
        if self._grabber is None:
            from .replayer import Grabber
            self._grabber = Grabber(headless=False).__enter__()
        return self._grabber

    # ---- 감시 스레드에서 호출하는 공개 API ----
    def ensure_login(self, log=None, timeout_s: int = 600) -> bool:
        if self.logged_in:
            return True
        if log:
            log("[브라우저] 로그인 확인 중(이미 로그인돼 있으면 자동 통과)…")

        def job():
            g = self._ensure_grabber()
            return g.ensure_login(timeout_s=timeout_s)

        ok = self._submit(job)
        if ok:
            self.logged_in = True
            self._login_lost_notified = False
            if log:
                log("[브라우저] 로그인 확인 완료 — 세션 유지됨")
        return ok

    # 하드 실패(그 회차 자체가 없음/진입 불가) → 탭 닫고 재사용 안 함.
    _HARD_FAIL = ("못 찾음", "전환 안", "재생 오류", "없습니다", "비어있습니다")

    def grab(self, recipe, day, hhmm, movie, persons, preferred, only, prefer, key=None):
        """좌석 선점 시도. key가 있으면 그 대상 전용 탭을 계속 재사용(껐다켰다 방지).
        성공→탭 유지(결제창), 소프트실패(좌석 대기)→탭 유지 재확인, 하드실패→탭 닫음.
        (ok, seat, msg) 반환."""
        def job():
            g = self._ensure_grabber()
            page = None
            if key is not None:
                page = self._pages.get(key)
                if page is not None:
                    try:
                        if page.is_closed():
                            page = None
                    except Exception:  # noqa: BLE001
                        page = None
            if page is None:
                slot = self._slots.get(key)
                if slot is None:
                    slot = self._next_slot
                    self._next_slot += 1
                    if key is not None:
                        self._slots[key] = slot
                page = g.new_window(slot)   # 대상마다 '별도 창'
                if key is not None:
                    self._pages[key] = page
            try:
                ok, seat, msg = g.replay(recipe, day=day, hhmm=hhmm, movie=movie, persons=persons,
                                         preferred=preferred, only_preferred=only, prefer=prefer, page=page)
            except Exception:
                self._drop_page(key, page)
                raise
            if ok:
                if key is not None:
                    self._pages.pop(key, None)   # 결제창은 별도 유지(재사용 목록에서 제외)
            else:
                hard = (key is None) or any(h in (msg or "") for h in self._HARD_FAIL)
                if hard:
                    self._drop_page(key, page)
                # 소프트 실패(원하는 좌석 대기 등): 탭 유지 → 다음 사이클 같은 탭에서 재확인
            return ok, seat, msg
        return self._submit(job)

    def _drop_page(self, key, page):
        try:
            page.close()
        except Exception:  # noqa: BLE001
            pass
        if key is not None:
            self._pages.pop(key, None)

    def keepalive(self) -> None:
        """세션 유휴 만료 방지 — 컨트롤 탭을 예매 페이지로 새로고침(활동 신호)."""
        if self.held.is_set() or self._grabber is None or not self.logged_in:
            return

        def job():
            from .replayer import CINEMA_URL
            try:
                self._grabber.page.goto(CINEMA_URL, wait_until="domcontentloaded")
            except Exception:  # noqa: BLE001
                pass

        try:
            self._submit(job)
        except Exception:  # noqa: BLE001
            pass

    def recheck_login(self, log=None, notifier=None) -> bool:
        if self.held.is_set() or not self.logged_in:
            return True

        def job():
            if self._grabber is None:
                return True
            return self._grabber.quick_login_check()

        try:
            ok = self._submit(job)
        except Exception:  # noqa: BLE001
            return True
        if not ok:
            self.logged_in = False
            if not self._login_lost_notified:
                self._login_lost_notified = True
                if log:
                    log("[로그인] ⚠️ CGV 세션이 만료된 것 같아요. '① 로그인 준비'로 다시 로그인하세요.")
                if notifier:
                    try:
                        notifier.notify_info("🔑 CGV 로그인 필요",
                                             "세션이 만료됐어요. 프로그램에서 '① 로그인 준비'를 눌러 다시 로그인하세요.")
                    except Exception:  # noqa: BLE001
                        pass
            return False
        return True

    def can_hold(self) -> bool:
        with self._holds_lock:
            return self.holds < self.max_holds

    def add_hold(self) -> int:
        with self._holds_lock:
            self.holds += 1
            return self.holds

    def close(self) -> None:
        self._closed = True

        def job():
            if self._grabber:
                try:
                    self._grabber.close()
                except Exception:  # noqa: BLE001
                    pass
            self._grabber = None

        try:
            self._submit(job)
        except Exception:  # noqa: BLE001
            pass
        self._q.put(None)
        self.logged_in = False
        self._login_lost_notified = False
        with self._holds_lock:
            self.holds = 0
        self.held.clear()
