"""
감시: 대상 회차의 취소표/오픈을 무인증 API로 감지 → 재생기로 좌석 자동 잡기.

흐름: 로그인(1회) → 반복 [API로 잔여석 확인 → 예매가능하면 replay 로 좌석 잡기].
원하는 좌석(only_preferred)이면 그 좌석이 뜰 때까지 잡지 않고 계속 감시.
"""
from __future__ import annotations

import logging
import os
import random
import re
import time
from contextlib import nullcontext

from . import cgv_api, paths
from .replayer import Grabber

logger = logging.getLogger("cgv_macro")
_NULL_LOCK = nullcontext()


def _scnymd(date: str) -> str:
    return (date or "").replace("-", "").replace(".", "").strip()


def _in_window(hhmm: str, tfrom: str, tto: str) -> bool:
    """회차 시간이 [tfrom, tto] 범위 안인지(빈 값은 무제한)."""
    if tfrom and hhmm < tfrom:
        return False
    if tto and hhmm > tto:
        return False
    return True


def _shot_prefix(tag: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", tag)[:16] or "w"


class Watcher:
    """대상 1개를 '독립된 크롬 창 1개'에서 감시·선점. 성공하면 그 창을 결제창으로 유지."""

    def __init__(self, recipe: dict, target: dict, notifier=None,
                 storage_state: str | None = None, win_pos=None, tag: str = "감시",
                 grabbed_keys: set | None = None, on_success=None, relogin_cfg=None) -> None:
        self.recipe = recipe
        self.t = target
        self.notifier = notifier
        self.storage_state = storage_state   # 저장된 로그인 세션(공유 로그인)
        self.win_pos = win_pos
        self.tag = tag
        self.grabbed_keys = grabbed_keys if grabbed_keys is not None else set()  # 중복 예매 방지(공유)
        self.on_success = on_success         # 성공 시 콜백(소리/팝업/영속화)
        self.relogin_cfg = relogin_cfg or {}  # 자동 재로그인 설정(토글/아이디/비번/2captcha)
        self.grabber: Grabber | None = None
        self.held_payment = False   # 좌석 선점(결제창 도달) 여부
        self._relogin_notified = False
        self._parked = False        # 좌석표에 머무는 중(빠른 재확인 경로)

    def _ensure_logged_in(self, log, allow_manual=True) -> bool:
        """로그인 상태 확인. 풀렸으면 자동 재로그인(켜져 있으면) 시도, 아니면 '수동 대기'.
        수동 대기 시에는 창을 다시 이동시키지 않아 사용자의 로그인 입력이 초기화되지 않는다."""
        try:
            if self.grabber.quick_login_check():   # 로그인 판정(여기서 로그인 페이지로 1회 이동)
                return True
        except Exception:  # noqa: BLE001
            return True   # 판단 불가 시 진행
        cfg = self.relogin_cfg
        if cfg.get("enabled") and cfg.get("cgv_id") and cfg.get("cgv_pw"):
            from .relogin import attempt_relogin
            log(f"[{self.tag}] 세션 만료 감지 → 자동 재로그인 시도")
            if attempt_relogin(self.grabber.page, cfg.get("cgv_id", ""), cfg.get("cgv_pw", ""),
                               cfg.get("twocaptcha", ""), log=log):
                self._relogin_notified = False
                return True
        if not allow_manual:
            return False
        if not self._relogin_notified:
            self._relogin_notified = True
            log(f"[{self.tag}] 로그인이 필요합니다 — 이 창에서 직접 로그인하세요(입력이 초기화되지 않습니다). "
                f"여러 창이면 중지 후 '① 로그인 준비'로 한 번에 로그인하는 게 편합니다")
            if self.notifier:
                try:
                    self.notifier.notify_info("🔑 CGV 로그인 필요",
                                              "이 창에서 로그인하면 감시가 이어집니다(자동 재로그인을 켜면 다음부턴 자동).")
                except Exception:  # noqa: BLE001
                    pass
        # 이미 quick_login_check 가 로그인 페이지로 이동해둔 상태 → 다시 이동하지 않고 수동 로그인만 기다림
        try:
            ok = self.grabber.wait_login_passive(timeout_s=600)
            if ok:
                self._relogin_notified = False
            return ok
        except Exception:  # noqa: BLE001
            return False

    def run(self, stop_event, log=None) -> None:
        log = log or logger.info
        tag = self.tag
        t = self.t
        try:
            mov_no, mov_nm = cgv_api.resolve_movie(t.get("movie", ""), t.get("movie_code", ""))
            site_no, site_nm, _region = cgv_api.resolve_theater(t.get("theater", ""), t.get("theater_code", ""))
        except Exception as e:  # noqa: BLE001
            log(f"[{tag}] 영화/극장 해석 실패: {e}")
            return
        target_time = (t.get("time") or "").strip()
        tfrom = (t.get("time_from") or "").strip()
        tto = (t.get("time_to") or "").strip()
        persons = {k: int(v) for k, v in (t.get("persons") or {"일반": 2}).items() if int(v) > 0}
        need = sum(persons.values()) or 1
        preferred = t.get("preferred") or []
        only_pref = bool(t.get("only_preferred"))
        prefer = t.get("prefer", "center")
        interval = max(5, int(t.get("interval", 10)))
        screen_type = (t.get("screen_type") or "").strip()
        date_key = t.get("date", "")
        dup_key = f"{mov_no}|{site_no}|{_scnymd(date_key)}|{target_time}"

        log(f"[{tag}] 창 열기: {mov_nm} / {site_nm} / {date_key} "
            f"{target_time or (tfrom+'~'+tto if (tfrom or tto) else '(전체 회차)')} / 좌석 {need}석 "
            f"{'/ 원하는좌석 '+','.join(preferred) if preferred else ''}")

        if dup_key in self.grabbed_keys:
            log(f"[{tag}] 이미 예매한 회차 — 건너뜀(중복 예매 방지)")
            return

        try:
            self.grabber = Grabber(headless=False, storage_state=self.storage_state,
                                   win_pos=self.win_pos, shot_prefix=_shot_prefix(tag)).__enter__()
        except Exception as e:  # noqa: BLE001
            log(f"[{tag}] 크롬 실행 실패: {e}")
            return
        # 감시(자리 확인)는 로그인 불필요(API). 창은 예매 페이지만 띄워두고 바로 감시 시작.
        # 로그인은 '좌석 잡을 때'만 필요하므로, 시작 시엔 로그인 확인/이동을 하지 않는다.
        try:
            self.grabber.page.goto("https://cgv.co.kr/cnm/movieBook/cinema", wait_until="domcontentloaded")
        except Exception:  # noqa: BLE001
            pass
        log(f"[{tag}] 감시 시작 (로그인은 예매 시점에만 확인)")

        idle_ticks = 0
        backoff = 0
        while not stop_event.is_set():
            try:
                shows = cgv_api.fetch_showtimes(mov_no, site_no, _scnymd(date_key))
                backoff = 0
            except cgv_api.CgvRateLimited:
                backoff = min(backoff + 1, 6)
                wait = min(20 * (2 ** backoff), 600)   # 40s→...→최대 10분
                log(f"[{tag}] 요청 과다(429) — {wait}s 대기 후 재시도")
                self._sleep(wait, stop_event)
                continue
            except Exception as e:  # noqa: BLE001
                log(f"[{tag}] 조회 오류: {e}")
                self._sleep(interval, stop_event)
                continue

            cands = [
                s for s in shows
                if s.remaining >= need
                and (not target_time or s.time == target_time)
                and _in_window(s.time, tfrom, tto)
                and (not screen_type or screen_type.lower() in (s.screen + " " + s.fmt).lower())
            ]
            if cands:
                s = cands[0]
                log(f"[{tag}] 예매가능 감지: {s.time} {s.screen} 잔여{s.remaining} → 좌석 잡기 시도")
                try:
                    # 2번: 좌석표에 머물러 있으면(직전 '대기') 새로고침만으로 빠르게 재확인
                    if only_pref and self._parked:
                        ok, seat, msg = self.grabber.seatmap_recheck(
                            self.grabber.page, need, preferred, only_pref, prefer)
                        if msg == "NEED_FULL":
                            self._parked = False
                            ok, seat, msg = self.grabber.replay(
                                self.recipe, day=date_key, hhmm=s.time, movie=mov_nm,
                                persons=persons, preferred=preferred, only_preferred=only_pref, prefer=prefer)
                    else:
                        ok, seat, msg = self.grabber.replay(
                            self.recipe, day=date_key, hhmm=s.time, movie=mov_nm,
                            persons=persons, preferred=preferred, only_preferred=only_pref, prefer=prefer)
                    # 원하는 좌석 대기 상태면 좌석표에 머무른 것 → 다음엔 빠른 재확인
                    self._parked = bool(only_pref and (not ok) and ("대기중" in (msg or "")))
                except Exception as e:  # noqa: BLE001
                    ok, seat, msg = False, "", f"좌석잡기 오류: {e}"
                    self._parked = False
                if ok:
                    self.held_payment = True
                    self.grabbed_keys.add(dup_key)
                    log(f"[{tag}] ✅ 좌석 선점 완료: {seat} — {msg} (이 창은 결제용으로 유지)")
                    shot = os.path.join(paths.data_dir(), f"grab_{_shot_prefix(tag)}payment.png")
                    if self.notifier:
                        try:
                            self.notifier.notify_held_image(
                                movie=mov_nm, theater=site_nm, date=date_key, showtime=s.time,
                                screen=f"{s.screen} ({s.fmt})", seat_info=seat,
                                image_path=shot if os.path.exists(shot) else "")
                        except Exception:  # noqa: BLE001
                            pass
                    if self.on_success:
                        try:
                            self.on_success({"tag": tag, "movie": mov_nm, "theater": site_nm,
                                             "date": date_key, "time": s.time, "seat": seat,
                                             "key": dup_key})
                        except Exception:  # noqa: BLE001
                            pass
                    break   # 이 창은 결제창으로 남기고 이 대상 감시 종료
                else:
                    log(f"[{tag}] 미완료: {msg}")
                    # 로그인/대기열 문제로 실패했을 때만 로그인 확인/복구(자동 또는 수동 대기)
                    if any(w in (msg or "") for w in ("로그인", "전환 안", "대기열")):
                        self._parked = False
                        self._ensure_logged_in(log)
            else:
                # 예매가능 회차 없음 — 조용히 대기. 가끔 keep-alive(단, 로그인 화면이면 절대 안 건드림).
                idle_ticks += 1
                if idle_ticks % 12 == 0:
                    try:
                        cur = (self.grabber.page.url or "").lower()
                    except Exception:  # noqa: BLE001
                        cur = ""
                    if "login" not in cur:   # 사용자가 로그인 중인 창은 이동시키지 않음
                        try:
                            self.grabber.page.goto("https://cgv.co.kr/cnm/movieBook/cinema",
                                                   wait_until="domcontentloaded")
                        except Exception:  # noqa: BLE001
                            pass
                        log(f"[{tag}] 대기중(세션 유지)")
            # 주기에 지터를 섞어 여러 창이 동시에 요청하지 않게(차단 회피)
            self._sleep(interval + random.uniform(0, min(5.0, interval * 0.4)), stop_event)
        log(f"[{tag}] 종료")

    @staticmethod
    def _sleep(seconds: int, stop_event) -> None:
        waited = 0.0
        while waited < seconds and not stop_event.is_set():
            time.sleep(0.5)
            waited += 0.5

    def close(self) -> None:
        if self.grabber:
            try:
                self.grabber.close()
            except Exception:  # noqa: BLE001
                pass


class MultiWatcher:
    """여러 대상을 로그인된 크롬 1개로 동시 감시 → 자리가 뜬 것부터 잡기(예매는 하나씩)."""

    def __init__(self, recipe: dict, targets: list[dict], notifier=None, hub=None) -> None:
        self.recipe = recipe
        self.targets = targets
        self.notifier = notifier
        self.hub = hub                       # 공유 브라우저 허브(있으면 크롬/락 공유)
        self.grabber: Grabber | None = None
        self.grabbed: set[str] = set()       # 이미 선점한 회차키(중복 선점 방지)

    def run(self, stop_event, log=None) -> None:
        log = log or logger.info
        # 대상별 코드 미리 해석 + 파라미터 정규화
        specs = []
        for t in self.targets:
            try:
                mov_no, mov_nm = cgv_api.resolve_movie(t.get("movie", ""), t.get("movie_code", ""))
                site_no, site_nm, _r = cgv_api.resolve_theater(t.get("theater", ""), t.get("theater_code", ""))
            except Exception as e:  # noqa: BLE001
                log(f"[감시] '{t.get('movie')}/{t.get('theater')}' 해석 실패: {e}")
                continue
            persons = {k: int(v) for k, v in (t.get("persons") or {"일반": 2}).items() if int(v) > 0}
            ymd = _scnymd(t.get("date", ""))
            tm = (t.get("time") or "").strip()
            scr = (t.get("screen_type") or "").strip()
            specs.append({
                "t": t, "mov_no": mov_no, "mov_nm": mov_nm, "site_no": site_no, "site_nm": site_nm,
                "ymd": ymd, "time": tm, "screen": scr,
                "persons": persons, "need": sum(persons.values()) or 1,
                "preferred": t.get("preferred") or [], "only": bool(t.get("only_preferred")),
                "prefer": t.get("prefer", "center"),
                "key": f"{mov_no}|{site_no}|{ymd}|{tm}|{scr}",   # 대상 전용 탭 키
            })
        if not specs:
            log("[감시] 유효한 대상이 없습니다."); return
        interval = max(5, min(int(x["t"].get("interval", 10)) for x in specs))
        log(f"[감시] 대상 {len(specs)}개 동시 감시 (주기 {interval}s):")
        for sp in specs:
            log(f"   · {sp['mov_nm']} / {sp['site_nm']} / {sp['t'].get('date')} "
                f"{sp['time'] or '(전체)'} {'/좌석 '+','.join(sp['preferred']) if sp['preferred'] else ''}")

        if self.hub is not None:
            if not self.hub.ensure_login(log):
                log("[감시] 로그인 실패 — 중지"); return
        else:
            self.grabber = Grabber(headless=False).__enter__()
            log("[감시] 크롬에 로그인하세요(이미 되어있으면 자동 통과)...")
            if not self.grabber.ensure_login(timeout_s=600):
                log("[감시] 로그인 실패 — 중지"); return
        log("[감시] 로그인 확인 → 감시 시작")

        while not stop_event.is_set() and not self._held():
            for sp in specs:
                if stop_event.is_set() or self._held():
                    break
                try:
                    shows = cgv_api.fetch_showtimes(sp["mov_no"], sp["site_no"], sp["ymd"])
                except Exception as e:  # noqa: BLE001
                    log(f"[감시] {sp['mov_nm']} 조회 오류: {e}")
                    continue
                cands = [
                    s for s in shows
                    if s.remaining >= sp["need"]
                    and (not sp["time"] or s.time == sp["time"])
                    and (not sp["screen"] or sp["screen"].lower() in (s.screen + " " + s.fmt).lower())
                    and s.showtime_key() not in self.grabbed
                ]
                if not cands:
                    continue
                s = cands[0]
                # 동시 선점 상한 체크
                if self.hub is not None and not self.hub.can_hold():
                    log(f"[감시] 동시 선점 상한({self.hub.max_holds}) 도달 — 결제/닫기 후 재개")
                    continue
                log(f"[감시] ▶ {sp['mov_nm']} {s.time} {s.screen} 잔여{s.remaining} → 좌석 잡기")
                self.grabbed.add(s.showtime_key())   # 같은 회차 중복 선점 방지
                if self._held():
                    break
                # 브라우저 작업은 허브(전용 스레드)로 위임 → 새 탭에서 선점(동시 선점 가능)
                try:
                    if self.hub is not None:
                        ok, seat, msg = self.hub.grab(
                            self.recipe, sp["t"].get("date", ""), s.time, sp["mov_nm"],
                            sp["persons"], sp["preferred"], sp["only"], sp["prefer"],
                            key=sp["key"])
                    else:
                        ok, seat, msg = self.grabber.replay(
                            self.recipe, day=sp["t"].get("date", ""), hhmm=s.time, movie=sp["mov_nm"],
                            persons=sp["persons"], preferred=sp["preferred"],
                            only_preferred=sp["only"], prefer=sp["prefer"])
                except Exception as e:  # noqa: BLE001
                    ok, seat, msg = False, "", f"좌석잡기 오류: {e}"
                if ok:
                    n = self.hub.add_hold() if self.hub is not None else 1
                    log(f"[감시] ✅ 좌석 선점: {sp['mov_nm']} {seat} — {msg} (선점 {n}개, 감시 계속)")
                    if self.notifier:
                        try:
                            self.notifier.notify_showtime(
                                kind="seat_held", target_name=sp["mov_nm"], movie=sp["mov_nm"],
                                theater=sp["site_nm"], date=sp["t"].get("date", ""), showtime=s.time,
                                screen=f"{s.screen} ({s.fmt})", status_text=msg, seat_info=seat,
                                booking_url="https://cgv.co.kr/cnm/movieBook/cinema")
                        except Exception:  # noqa: BLE001
                            pass
                else:
                    self.grabbed.discard(s.showtime_key())   # 실패 → 재시도 허용
                    log(f"[감시] {sp['mov_nm']} 미완료: {msg}")
            self._sleep_all(interval, stop_event)
        log("[감시] 종료")

    def _held(self) -> bool:
        return self.hub is not None and self.hub.held.is_set()

    def _sleep_all(self, seconds: int, stop_event) -> None:
        waited = 0.0
        while waited < seconds and not stop_event.is_set() and not self._held():
            time.sleep(0.5)
            waited += 0.5

    def close(self) -> None:
        if self.grabber and self.hub is None:   # 허브 소유 브라우저는 허브가 닫는다
            try:
                self.grabber.close()
            except Exception:  # noqa: BLE001
                pass
