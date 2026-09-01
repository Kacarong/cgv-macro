"""
CGV 좌석 감시 · 자동잡기 — 다크 UI (CustomTkinter).

탭: 취소표 감지 / 상영 오픈 감지 / 무대인사 감지 / 설정.
- 감지는 전부 CGV 조회 API 폴링(로그인·창 불필요). 실제 좌석잡기 때만 크롬 1개를 연다.
- 모든 감시는 크롬 1개(WatchHub)를 공유하고, 좌석잡기는 락으로 직렬화 → 창 충돌 없음.
- '로그인 준비'로 1회 로그인하면 세션이 프로필에 저장돼, 자리에 없어도 자동으로 잡는다.
"""
from __future__ import annotations

import datetime
import json
import os
import queue
import threading

import customtkinter as ctk

from cgv_macro import paths, cgv_api
from cgv_macro.logger import setup_logger

RED = "#E03A34"
RED_DK = "#B92C27"
GREEN = "#8FE388"
PANEL = "#1B1B1F"
BG = "#0F0F10"
SUB = "#9A9AA2"

CONFIG = os.path.join(paths.data_dir(), "app_config.json")
RECIPE = os.path.join(paths.data_dir(), "recipe.json")
SCREENS = ["전체", "2D", "IMAX", "4DX", "SCREENX", "DOLBY ATMOS", "ULTRA 4DX"]
WD = ["월", "화", "수", "목", "금", "토", "일"]


def _gen_dates(n=21):
    out = []
    today = datetime.date.today()
    for i in range(n):
        d = today + datetime.timedelta(days=i)
        label = f"{d.month:02d}/{d.day:02d}({WD[d.weekday()]})" + (" 오늘" if i == 0 else "")
        out.append((label, d.strftime("%Y-%m-%d")))
    return out


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("CGV SEAT WATCHER")
        self.geometry("860x900")
        self.configure(fg_color=BG)

        self.log_q: "queue.Queue[str]" = queue.Queue()
        self.rec_thread = None
        self.rec_start = threading.Event()
        self.rec_stop = threading.Event()
        self.watch_stop = threading.Event()
        self.hub = None
        self.workers: list = []
        self._active = 0
        self._notifier = None
        self._wd_tick = 0

        self.cancel_list: list[dict] = []      # 취소표 감지 대상
        self.open_list: list[dict] = []        # 상영오픈 감지 대상
        self.event_theaters: list[str] = []    # 무대인사 감지 극장(이름)
        self.panels: list[dict] = []           # 영화/극장 메뉴 갱신 대상
        self.movies: dict[str, str] = {}
        self.theaters: dict[str, tuple] = {}
        self.dates = _gen_dates()
        self.date_map = {lbl: val for lbl, val in self.dates}

        self._build()
        self._load()
        self.logger = setup_logger(paths.logs_dir(), "INFO")
        self.after(200, self._drain)
        self.after(300000, self._login_watchdog)   # 5분마다 로그인 상태 점검
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        threading.Thread(target=self._load_lists, daemon=True).start()

    # ---------- UI helpers ----------
    def _card(self, parent, title=None):
        c = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=14)
        c.pack(fill="x", padx=16, pady=8)
        if title:
            ctk.CTkLabel(c, text=title, text_color="white",
                         font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=16, pady=(12, 2))
        return c

    def _note(self, parent, text):
        ctk.CTkLabel(parent, text=text, text_color=SUB, justify="left", wraplength=760,
                     font=ctk.CTkFont(size=12)).pack(anchor="w", padx=20, pady=(6, 2))

    def _dd(self, parent, values, width=None):
        kw = dict(values=values, fg_color="#2A2A30", button_color=RED, button_hover_color=RED_DK)
        if width:
            kw["width"] = width
        return ctk.CTkOptionMenu(parent, **kw)

    def _ps_card(self, parent, ns):
        """인원·좌석·주기 위젯을 ns에 채운다."""
        pc = self._card(parent, "인원 · 좌석")
        prow = ctk.CTkFrame(pc, fg_color=PANEL); prow.pack(fill="x", padx=16, pady=(4, 4))
        ns["gen"] = self._dd(prow, [str(i) for i in range(0, 9)], 64)
        ns["teen"] = self._dd(prow, [str(i) for i in range(0, 9)], 64)
        ns["pref"] = self._dd(prow, [str(i) for i in range(0, 9)], 64)
        ns["gen"].set("2")
        for t, w in [("일반", ns["gen"]), ("청소년", ns["teen"]), ("우대", ns["pref"])]:
            ctk.CTkLabel(prow, text=t, text_color=SUB).pack(side="left", padx=(10, 3)); w.pack(side="left")

        srow = ctk.CTkFrame(pc, fg_color=PANEL); srow.pack(fill="x", padx=16, pady=(4, 12))
        ctk.CTkLabel(srow, text="원하는 좌석", text_color=SUB).pack(side="left", padx=(6, 4))
        ns["seats"] = ctk.CTkEntry(srow, placeholder_text="예: E9,E10 (비우면 자동)", width=160, fg_color="#2A2A30")
        ns["seats"].pack(side="left")
        ns["only"] = ctk.CTkSwitch(srow, text="이 좌석만", progress_color=RED)
        ns["only"].pack(side="left", padx=10)
        ctk.CTkLabel(srow, text="위치", text_color=SUB).pack(side="left", padx=(10, 3))
        ns["pos"] = self._dd(srow, ["center", "front", "back", "any"], 90); ns["pos"].pack(side="left")
        ctk.CTkLabel(srow, text="주기(초)", text_color=SUB).pack(side="left", padx=(10, 3))
        ns["int"] = self._dd(srow, ["5", "10", "15", "30", "60"], 70); ns["int"].set("10"); ns["int"].pack(side="left")

    def _target_panel(self, parent):
        """영화/극장/날짜/상영관/회차 + 인원·좌석. ns dict 반환(메뉴 갱신용으로 self.panels 등록)."""
        ns: dict = {"time_map": {}}
        tc = self._card(parent, "대상 선택")
        grid = ctk.CTkFrame(tc, fg_color=PANEL); grid.pack(fill="x", padx=16, pady=(4, 12))
        for i in range(4):
            grid.grid_columnconfigure(i, weight=1)

        def lab(r, cc, t):
            ctk.CTkLabel(grid, text=t, text_color=SUB, font=ctk.CTkFont(size=11)).grid(
                row=r, column=cc, sticky="w", padx=6, pady=(6, 0))

        ns["movie"] = self._dd(grid, ["불러오는 중…"])
        ns["theater"] = self._dd(grid, ["불러오는 중…"])
        ns["date"] = self._dd(grid, [l for l, _ in self.dates])
        ns["screen"] = self._dd(grid, SCREENS)
        lab(0, 0, "영화"); ns["movie"].grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
        lab(0, 1, "극장"); ns["theater"].grid(row=1, column=1, sticky="ew", padx=6, pady=(0, 6))
        lab(0, 2, "날짜"); ns["date"].grid(row=1, column=2, sticky="ew", padx=6, pady=(0, 6))
        lab(0, 3, "상영관"); ns["screen"].grid(row=1, column=3, sticky="ew", padx=6, pady=(0, 6))
        lab(2, 0, "회차(시간)")
        ns["time"] = self._dd(grid, ["전체(자동)"])
        ns["time"].grid(row=3, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 6))
        ctk.CTkButton(grid, text="회차 불러오기", fg_color="#33333A", hover_color="#44444C",
                      command=lambda: self._load_times(ns)).grid(row=3, column=2, sticky="ew", padx=6, pady=(0, 6))

        self._ps_card(parent, ns)
        self.panels.append(ns)
        return ns

    def _list_card(self, parent, title, mode):
        """대상 목록 카드(추가/비우기 + 스크롤 리스트). 리스트 프레임을 반환."""
        lc = self._card(parent, title)
        addrow = ctk.CTkFrame(lc, fg_color=PANEL); addrow.pack(fill="x", padx=16, pady=(4, 4))
        ctk.CTkButton(addrow, text="＋ 현재 설정을 목록에 추가", fg_color=RED, hover_color=RED_DK,
                      command=lambda: self._add_to(mode)).pack(side="left")
        ctk.CTkButton(addrow, text="목록 비우기", fg_color="#33333A", hover_color="#44444C",
                      command=lambda: self._clear_list(mode)).pack(side="left", padx=8)
        frame = ctk.CTkScrollableFrame(lc, fg_color="#141417", height=130)
        frame.pack(fill="x", padx=16, pady=(2, 12))
        return frame

    def _build(self):
        # 헤더
        head = ctk.CTkFrame(self, fg_color=BG, height=64)
        head.pack(fill="x", padx=16, pady=(14, 2))
        ctk.CTkLabel(head, text="●", text_color=RED, font=ctk.CTkFont(size=22)).pack(side="left")
        ctk.CTkLabel(head, text="CGV Seat Watcher", text_color="white",
                     font=ctk.CTkFont(size=22, weight="bold")).pack(side="left", padx=6)
        ctk.CTkLabel(head, text="취소표·오픈·무대인사 감지 → 좌석 자동 잡기", text_color=SUB,
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=10)

        # === 하단 고정: 액션바 + 로그 (항상 보이게) ===
        actionbar = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=0)
        actionbar.pack(side="bottom", fill="x")
        ctk.CTkButton(actionbar, text="① 로그인 준비", fg_color="#33333A", hover_color="#44444C",
                      width=110, command=self._prelogin).pack(side="left", padx=(14, 6), pady=10)
        ctk.CTkButton(actionbar, text="설정 저장", fg_color="#33333A", hover_color="#44444C",
                      width=80, command=self._save).pack(side="left", padx=(0, 6), pady=10)
        self.b_start = ctk.CTkButton(actionbar, text="② 감시 시작", fg_color=RED, hover_color=RED_DK,
                                     font=ctk.CTkFont(size=14, weight="bold"), width=140, height=38,
                                     command=self._watch_start)
        self.b_start.pack(side="left", padx=6, pady=10)
        self.b_stop = ctk.CTkButton(actionbar, text="중지", fg_color="#33333A", hover_color="#44444C",
                                    width=70, height=38, state="disabled", command=self._watch_stop)
        self.b_stop.pack(side="left", pady=10)
        self.stat = ctk.CTkLabel(actionbar, text="● 대기", text_color=SUB); self.stat.pack(side="right", padx=16)

        self.logbox = ctk.CTkTextbox(self, fg_color="#050506", text_color="#D6D6DA",
                                     font=ctk.CTkFont(size=12), height=130, corner_radius=0)
        self.logbox.pack(side="bottom", fill="x")
        self.logbox.configure(state="disabled")

        # === 탭 ===
        self.tabs = ctk.CTkTabview(self, fg_color=BG, segmented_button_selected_color=RED,
                                   segmented_button_selected_hover_color=RED_DK)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=(2, 4))
        t_cancel = ctk.CTkScrollableFrame(self.tabs.add("취소표 감지"), fg_color=BG)
        t_cancel.pack(fill="both", expand=True)
        t_open = ctk.CTkScrollableFrame(self.tabs.add("상영 오픈 감지"), fg_color=BG)
        t_open.pack(fill="both", expand=True)
        t_event = ctk.CTkScrollableFrame(self.tabs.add("무대인사 감지"), fg_color=BG)
        t_event.pack(fill="both", expand=True)
        sett = ctk.CTkScrollableFrame(self.tabs.add("설정"), fg_color=BG)
        sett.pack(fill="both", expand=True)

        # --- 취소표 감지 탭 ---
        self._note(t_cancel, "매진된 회차의 잔여석(취소표)을 API로 상시 확인합니다. 자리가 나면 크롬을 열어\n"
                             "원하는 인원·좌석으로 결제하기까지 자동 선점합니다. 여러 대상을 동시에 감시할 수 있어요.")
        self.ns_cancel = self._target_panel(t_cancel)
        self.cancel_frame = self._list_card(t_cancel, "취소표 감시 대상 (여러 개 · 동시 감시)", "취소표")

        # --- 상영 오픈 감지 탭 ---
        self._note(t_open, "아직 열리지 않은 회차가 예매 오픈되는 순간을 API로 감지해 바로 잡습니다.\n"
                           "회차 시간은 '전체(자동)'로 두면 오픈되는 첫 회차를 잡습니다.")
        self.ns_open = self._target_panel(t_open)
        self.open_frame = self._list_card(t_open, "상영오픈 감시 대상 (여러 개 · 동시 감시)", "상영오픈")

        # --- 무대인사 감지 탭 ---
        self._note(t_event, "영화를 지정하지 않고, 선택한 극장의 '상영중 전체영화'를 매 사이클 새로 불러와(최신화)\n"
                            "무대인사/GV/내한/관객과의 대화를 감지합니다. 개봉 전 영화도 예매가 열리는 순간 자동 포착.\n"
                            "전 극장 스캔은 차단(429) 위험이 커서 극장 지정식입니다.")
        ec = self._card(t_event, "무대인사 감지 극장 (여러 곳)")
        erow = ctk.CTkFrame(ec, fg_color=PANEL); erow.pack(fill="x", padx=16, pady=(4, 4))
        self.ev_theater = self._dd(erow, ["불러오는 중…"], 220); self.ev_theater.pack(side="left", padx=(6, 6))
        ctk.CTkButton(erow, text="＋ 극장 추가", fg_color=RED, hover_color=RED_DK,
                      command=self._add_event_theater).pack(side="left")
        ctk.CTkButton(erow, text="비우기", fg_color="#33333A", hover_color="#44444C",
                      command=self._clear_event_theaters).pack(side="left", padx=8)
        ctk.CTkLabel(erow, text="날짜범위", text_color=SUB).pack(side="left", padx=(12, 3))
        self.dd_days = self._dd(erow, ["3", "7", "14"], 70); self.dd_days.set("7"); self.dd_days.pack(side="left")
        self.ev_lbl = ctk.CTkLabel(ec, text="(선택된 극장 없음)", text_color=SUB, wraplength=740, justify="left")
        self.ev_lbl.pack(anchor="w", padx=16, pady=(2, 12))
        self.ns_event = {}
        self._ps_card(t_event, self.ns_event)

        # --- 설정 탭: 녹화 + 디스코드 ---
        rc = self._card(sett, "녹화 (최초 1회 · 로그인 + 예매 클릭 기록)")
        r1 = ctk.CTkFrame(rc, fg_color=PANEL); r1.pack(fill="x", padx=16, pady=(4, 12))
        self.b_rec = ctk.CTkButton(r1, text="녹화 시작하기", fg_color="#33333A", hover_color="#44444C",
                                   width=120, command=self._rec_open)
        self.b_rec.pack(side="left")
        self.b_rec_go = ctk.CTkButton(r1, text="① 로그인함·시작", fg_color=GREEN, text_color="black",
                                      hover_color="#7BD173", width=140, state="disabled",
                                      command=lambda: self.rec_start.set())
        self.b_rec_go.pack(side="left", padx=6)
        self.b_rec_end = ctk.CTkButton(r1, text="② 저장·종료", fg_color="#33333A", hover_color="#44444C",
                                       width=110, state="disabled", command=lambda: self.rec_stop.set())
        self.b_rec_end.pack(side="left")
        self.rec_stat = ctk.CTkLabel(r1, text="", text_color=SUB); self.rec_stat.pack(side="left", padx=12)

        dc = self._card(sett, "디스코드 알림 (선택)")
        drow = ctk.CTkFrame(dc, fg_color=PANEL); drow.pack(fill="x", padx=16, pady=(4, 12))
        self.e_hook = ctk.CTkEntry(drow, placeholder_text="웹훅 URL", width=430, fg_color="#2A2A30")
        self.e_hook.pack(side="left", padx=(6, 6))
        self.e_ment = ctk.CTkEntry(drow, placeholder_text="멘션 ID", width=140, fg_color="#2A2A30")
        self.e_ment.pack(side="left")

        self._note(sett, "권장 사용법: '① 로그인 준비'로 크롬을 띄워 1회 로그인하고 그 창을 그대로 두세요. 크롬 1개를 계속\n"
                         "재사용하므로 재로그인이 거의 필요 없습니다. '중지'해도 크롬은 로그인된 채 유지, 프로그램 종료 때만 닫힙니다.\n"
                         "\n"
                         "24시간 무인 운용: 앱이 2분마다 CGV 예매 페이지에 접속해 세션을 살려두고(keep-alive), 6분마다 로그인\n"
                         "상태를 점검해 풀리면 로그·디스코드로 '로그인 필요' 알림을 줍니다. (CGV는 자동로그인이 없어 keep-alive로 유지)\n"
                         "\n"
                         "동시 선점: 하나를 잡아도 감시는 계속되고, 잡을 때마다 '새 탭'으로 서로 다른 회차의 결제창을 동시에\n"
                         "유지합니다(최대 5개). 다만 CGV가 한 계정 동시 예매를 막으면 나중 것이 앞 것을 밀어낼 수 있어요(그땐 계정 분리 필요).")

    # ---------- 데이터 로드 ----------
    def _load_lists(self):
        try:
            movs = cgv_api.list_movies()
            self.movies = {nm: no for no, nm in movs}
            ths = cgv_api.list_theaters()
            self.theaters = {nm: (no, rg) for no, nm, rg in ths}
            self.after(0, self._fill_lists)
        except Exception as e:  # noqa: BLE001
            self._put(f"목록 불러오기 실패: {e}")

    def _fill_lists(self):
        mv = list(self.movies.keys()) or ["(없음)"]
        th = list(self.theaters.keys()) or ["(없음)"]
        default_th = "센텀시티" if "센텀시티" in th else th[0]
        for ns in self.panels:
            ns["movie"].configure(values=mv); ns["movie"].set(mv[0])
            ns["theater"].configure(values=th); ns["theater"].set(default_th)
        self.ev_theater.configure(values=th); self.ev_theater.set(default_th)

    def _load_times(self, ns):
        def worker():
            try:
                mv = self.movies.get(ns["movie"].get())
                th = self.theaters.get(ns["theater"].get())
                day = self.date_map.get(ns["date"].get())
                if not (mv and th and day):
                    self._put("영화/극장/날짜를 먼저 선택하세요."); return
                shows = cgv_api.fetch_showtimes(mv, th[0], day.replace("-", ""))
                screen = "" if ns["screen"].get() == "전체" else ns["screen"].get()
                if screen:
                    shows = [s for s in shows if screen.lower() in (s.screen + " " + s.fmt).lower()]
                labels = ["전체(자동)"]; ns["time_map"] = {}
                for s in shows:
                    ev = f"  🎤{s.event}" if getattr(s, "event", "") else ""
                    lb = f"{s.time}  {s.screen} 잔여{s.remaining}{ev}"
                    labels.append(lb); ns["time_map"][lb] = s.time
                self.after(0, lambda: (ns["time"].configure(values=labels), ns["time"].set(labels[0])))
                self._put(f"회차 {len(shows)}건 불러옴{(' ('+screen+')') if screen else ''}.")
            except Exception as e:  # noqa: BLE001
                self._put(f"회차 불러오기 실패: {e}")
        threading.Thread(target=worker, daemon=True).start()

    # ---------- 로그 ----------
    def _put(self, msg):
        self.log_q.put_nowait(str(msg))

    def _drain(self):
        try:
            while True:
                line = self.log_q.get_nowait()
                self.logbox.configure(state="normal")
                self.logbox.insert("end", line + "\n"); self.logbox.see("end")
                self.logbox.configure(state="disabled")
        except queue.Empty:
            pass
        self.rec_stat.configure(text=("녹화됨 ✓" if os.path.exists(RECIPE) else "녹화 없음"),
                                text_color=(GREEN if os.path.exists(RECIPE) else RED))
        self.after(300, self._drain)

    # ---------- 녹화 ----------
    def _rec_open(self):
        if self.workers and self._active > 0:
            self._put("감시 중에는 녹화 불가."); return
        from cgv_macro.recorder import run_recorder
        self.rec_start.clear(); self.rec_stop.clear()
        self.b_rec.configure(state="disabled")
        self.b_rec_go.configure(state="normal")
        self.b_rec_end.configure(state="normal", fg_color=RED, hover_color=RED_DK, text_color="white")

        def worker():
            try:
                run_recorder(self.rec_start, self.rec_stop, self._put)
            except Exception as e:  # noqa: BLE001
                self._put(f"[녹화] 오류: {e}")
            finally:
                self.after(0, self._rec_reset)
        self.rec_thread = threading.Thread(target=worker, daemon=True); self.rec_thread.start()
        self._put("[녹화] 크롬에서 로그인 후 '① 로그인함·시작'을 누르세요.")

    def _rec_reset(self):
        self.b_rec.configure(state="normal")
        self.b_rec_go.configure(state="disabled")
        self.b_rec_end.configure(state="disabled", fg_color="#33333A", text_color="white")

    # ---------- 설정 읽기/저장 ----------
    def _read_ps(self, ns) -> dict:
        return {
            "persons": {"일반": int(ns["gen"].get()), "청소년": int(ns["teen"].get()), "우대": int(ns["pref"].get())},
            "preferred": [s.strip() for s in ns["seats"].get().split(",") if s.strip()],
            "only_preferred": bool(ns["only"].get()),
            "prefer": ns["pos"].get(),
            "interval": int(ns["int"].get()),
        }

    def _read_target(self, ns, mode) -> dict:
        t = {
            "mode": mode,
            "movie": ns["movie"].get(),
            "theater": ns["theater"].get(),
            "date": self.date_map.get(ns["date"].get(), ""),
            "date_label": ns["date"].get(),
            "time": ns["time_map"].get(ns["time"].get(), ""),
            "time_label": ns["time"].get(),
            "screen_type": "" if ns["screen"].get() == "전체" else ns["screen"].get(),
            "webhook": self.e_hook.get().strip(),
            "mention": self.e_ment.get().strip(),
        }
        t.update(self._read_ps(ns))
        return t

    def _save(self):
        save_cfg({
            "cancel": self.cancel_list, "open": self.open_list,
            "event_theaters": self.event_theaters, "days": int(self.dd_days.get()),
            "webhook": self.e_hook.get().strip(), "mention": self.e_ment.get().strip(),
        })
        self._put("설정 저장됨.")

    def _load(self):
        c = load_cfg()
        if not c:
            self._refresh_target_list("취소표"); self._refresh_target_list("상영오픈")
            self._refresh_event_theaters(); return
        self.cancel_list = c.get("cancel", []) or []
        self.open_list = c.get("open", []) or []
        self.event_theaters = c.get("event_theaters", []) or []
        self._refresh_target_list("취소표"); self._refresh_target_list("상영오픈")
        self._refresh_event_theaters()
        if c.get("days"):
            self.dd_days.set(str(c.get("days")))
        if c.get("webhook"):
            self.e_hook.insert(0, c["webhook"])
        if c.get("mention"):
            self.e_ment.insert(0, c["mention"])

    # ---------- 대상 목록(취소표/상영오픈) ----------
    def _list_for(self, mode):
        return self.cancel_list if mode == "취소표" else self.open_list

    def _ns_for(self, mode):
        return self.ns_cancel if mode == "취소표" else self.ns_open

    def _frame_for(self, mode):
        return self.cancel_frame if mode == "취소표" else self.open_frame

    def _add_to(self, mode):
        ns = self._ns_for(mode)
        t = self._read_target(ns, mode)
        if not t["date"]:
            self._put("날짜를 선택하고 추가하세요."); return
        lst = self._list_for(mode)
        if len(lst) >= 8:
            self._put("대상은 최대 8개까지."); return
        lst.append(t); self._refresh_target_list(mode); self._save()
        self._put(f"[{mode}] 목록 추가: {t['movie']} / {t['theater']} / {t['date']} {t['time'] or '(전체)'}")

    def _remove_target(self, mode, idx):
        lst = self._list_for(mode)
        if 0 <= idx < len(lst):
            del lst[idx]; self._refresh_target_list(mode); self._save()

    def _clear_list(self, mode):
        if mode == "취소표":
            self.cancel_list = []
        else:
            self.open_list = []
        self._refresh_target_list(mode); self._save()

    def _refresh_target_list(self, mode):
        frame = self._frame_for(mode); lst = self._list_for(mode)
        for w in frame.winfo_children():
            w.destroy()
        if not lst:
            ctk.CTkLabel(frame, text="(비어있음 — 위 설정을 추가하세요)", text_color=SUB).pack(anchor="w", padx=6, pady=6)
            return
        for i, t in enumerate(lst):
            row = ctk.CTkFrame(frame, fg_color="#1E1E22", corner_radius=8)
            row.pack(fill="x", padx=4, pady=3)
            seats = (" · " + ",".join(t.get("preferred") or [])) if t.get("preferred") else ""
            txt = f"{i+1}. {t['movie']} · {t['theater']} · {t['date']} · {t['time'] or '전체'}{seats}"
            ctk.CTkLabel(row, text=txt, text_color="white").pack(side="left", padx=10, pady=6)
            ctk.CTkButton(row, text="삭제", width=48, fg_color="#3A2A2A", hover_color=RED,
                          command=lambda idx=i: self._remove_target(mode, idx)).pack(side="right", padx=6)

    # ---------- 무대인사 극장 ----------
    def _add_event_theater(self):
        nm = self.ev_theater.get()
        if nm in ("불러오는 중…", "(없음)", ""):
            self._put("극장 목록이 로드된 뒤 극장을 고르고 추가하세요."); return
        if nm not in self.event_theaters:
            self.event_theaters.append(nm); self._refresh_event_theaters(); self._save()
            self._put(f"무대인사 감지 극장 추가: {nm}")

    def _clear_event_theaters(self):
        self.event_theaters = []; self._refresh_event_theaters(); self._save()

    def _refresh_event_theaters(self):
        txt = ("· " + ",  ".join(self.event_theaters)) if self.event_theaters else "(선택된 극장 없음)"
        self.ev_lbl.configure(text=txt)

    # ---------- 로그인 준비 ----------
    def _prelogin(self):
        from cgv_macro.hub import WatchHub
        if self.hub is None:
            self.hub = WatchHub()
        self._put("[로그인] 크롬을 띄웁니다. 로그인 후 창은 그대로 두세요. 앱이 2분마다 세션을 살려둡니다(keep-alive).")

        def worker():
            try:
                ok = self.hub.ensure_login(self._put, timeout_s=600)
                if ok:
                    self._put("[로그인] 완료 — 세션 유지됨. '감시 시작'하면 재로그인 없이 진행됩니다.")
                    self.after(0, self._refresh_status)
                else:
                    self._put("[로그인] 실패/시간초과 — 다시 시도하세요.")
            except Exception as e:  # noqa: BLE001
                self._put(f"[로그인] 오류: {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _refresh_status(self):
        if self._active > 0:
            self.stat.configure(text="● 감시 중", text_color=GREEN)
        elif self.hub and self.hub.logged_in:
            self.stat.configure(text="● 대기(로그인 유지)", text_color=GREEN)
        else:
            self.stat.configure(text="● 대기", text_color=SUB)

    def _login_watchdog(self):
        # 2분마다 세션 유지(예매 페이지 새로고침), 6분마다 로그인 상태 점검(+풀리면 알림).
        if self.hub and self.hub.logged_in and not self.hub.held.is_set():
            self._wd_tick += 1
            tick = self._wd_tick

            def w():
                if tick % 3 == 0:
                    ok = self.hub.recheck_login(self._put, self._notifier)
                    if not ok:
                        self.after(0, lambda: self.stat.configure(text="● 로그인 필요", text_color=RED))
                else:
                    self.hub.keepalive()
            threading.Thread(target=w, daemon=True).start()
        self.after(120000, self._login_watchdog)   # 2분

    # ---------- 감시 ----------
    def _watch_start(self):
        if self._active > 0:
            return
        if not os.path.exists(RECIPE):
            self._put("먼저 '설정' 탭에서 예매 흐름을 1회 녹화하세요."); return
        recipe = json.load(open(RECIPE, encoding="utf-8"))
        webhook = self.e_hook.get().strip()
        notifier = None
        if webhook:
            from cgv_macro.notifier import DiscordNotifier
            notifier = DiscordNotifier(webhook, self.e_ment.get().strip())
        self._notifier = notifier

        from cgv_macro.hub import WatchHub
        if self.hub is None:
            self.hub = WatchHub()
        self.hub.held.clear()

        workers = []
        poll = list(self.cancel_list) + list(self.open_list)
        if poll:
            from cgv_macro.watcher import MultiWatcher
            workers.append(MultiWatcher(recipe, poll, notifier, hub=self.hub))
        if self.event_theaters:
            ths = [(self.theaters[nm][0], nm) for nm in self.event_theaters if nm in self.theaters]
            if ths:
                from cgv_macro.event_watch import EventWatcher
                base = self._read_ps(self.ns_event)
                workers.append(EventWatcher(recipe, ths, base, notifier,
                                            days=int(self.dd_days.get()), hub=self.hub))
        if not workers:
            self._put("감시할 대상이 없습니다. 각 탭에서 대상/극장을 추가하세요."); return

        self._save()
        self.watch_stop.clear()
        self.workers = workers
        self._active = len(workers)
        self._put(f"감시 시작 — 폴링대상 {len(poll)}개 / 무대인사극장 {len(self.event_theaters)}곳. "
                  f"크롬 로그인 상태 확인 중…")

        for w in workers:
            threading.Thread(target=self._run_worker, args=(w,), daemon=True).start()
        self.b_start.configure(state="disabled")
        self.b_stop.configure(state="normal", fg_color=RED, hover_color=RED_DK)
        self.stat.configure(text="● 감시 중", text_color=GREEN)

    def _run_worker(self, w):
        try:
            w.run(self.watch_stop, log=self._put)
        except Exception as e:  # noqa: BLE001
            self._put(f"[감시] 오류: {e}")
        finally:
            self.after(0, self._worker_done)

    def _worker_done(self):
        self._active -= 1
        if self._active <= 0:
            self._watch_ended()

    def _watch_stop(self):
        self.watch_stop.set()
        if self.hub:
            self.hub.held.set()  # 진행 중 좌석잡기 이후 즉시 정지 유도
        self.stat.configure(text="● 중지 중…", text_color="#E0A030")

    def _watch_ended(self):
        # 감시만 멈추고 크롬(로그인 세션)은 그대로 열어둔다 → 재시작 시 재로그인 불필요.
        # 브라우저는 프로그램 종료(_on_close) 때만 닫는다.
        for w in self.workers:
            try:
                w.close()   # hub 소유 브라우저는 닫지 않음(watcher.close 가 hub면 skip)
            except Exception:  # noqa: BLE001
                pass
        self.workers = []
        self.b_start.configure(state="normal")
        self.b_stop.configure(state="disabled", fg_color="#33333A")
        if self.hub and self.hub.logged_in:
            self.stat.configure(text="● 대기(로그인 유지)", text_color=GREEN)
            held = getattr(self.hub, "holds", 0)
            extra = f" 선점한 결제창 {held}개는 각 탭에서 결제하세요." if held else ""
            self._put(f"감시 중지 — 크롬은 로그인된 채 열려 있습니다.{extra} 다시 '감시 시작'하면 재로그인 없이 진행돼요.")
        else:
            self.stat.configure(text="● 대기", text_color=SUB)

    def _on_close(self):
        self.watch_stop.set(); self.rec_stop.set()
        if self.hub:
            self.hub.held.set()
            try:
                self.hub.close()   # 프로그램 종료 시에만 크롬 닫기
            except Exception:  # noqa: BLE001
                pass
        self.destroy()


def load_cfg() -> dict:
    if os.path.exists(CONFIG):
        try:
            return json.load(open(CONFIG, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def save_cfg(d: dict) -> None:
    json.dump(d, open(CONFIG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def main() -> int:
    App().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
