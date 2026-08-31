"""
CGV 좌석 감시 · 자동잡기 — 다크 UI (CustomTkinter).

모드: 취소표 감지 / 상영 오픈 감지.
영화·극장·날짜·상영관·회차를 드롭다운으로 선택. 감지 시 녹화 흐름대로 좌석 자동 잡기.
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
        self.geometry("820x860")
        self.configure(fg_color=BG)

        self.log_q: "queue.Queue[str]" = queue.Queue()
        self.rec_thread = None
        self.rec_start = threading.Event()
        self.rec_stop = threading.Event()
        self.watch_thread = None
        self.watch_stop = threading.Event()
        self.watcher = None

        self.movies: dict[str, str] = {}      # name -> movNo
        self.theaters: dict[str, tuple] = {}   # name -> (siteNo, region)
        self.dates = _gen_dates()
        self.date_map = {lbl: val for lbl, val in self.dates}
        self.time_map: dict[str, str] = {}     # label -> HH:MM

        self._build()
        self._load()
        self.logger = setup_logger(paths.logs_dir(), "INFO")
        self.after(200, self._drain)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        threading.Thread(target=self._load_lists, daemon=True).start()

    # ---------- UI ----------
    def _card(self, parent, title=None):
        c = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=14)
        c.pack(fill="x", padx=16, pady=8)
        if title:
            ctk.CTkLabel(c, text=title, text_color="white",
                         font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=16, pady=(12, 2))
        return c

    def _build(self):
        # 헤더
        head = ctk.CTkFrame(self, fg_color=BG, height=64)
        head.pack(fill="x", padx=16, pady=(14, 2))
        dot = ctk.CTkLabel(head, text="●", text_color=RED, font=ctk.CTkFont(size=22))
        dot.pack(side="left")
        ctk.CTkLabel(head, text="CGV Seat Watcher", text_color="white",
                     font=ctk.CTkFont(size=22, weight="bold")).pack(side="left", padx=6)
        ctk.CTkLabel(head, text="취소표·오픈 감지 → 좌석 자동 잡기", text_color=SUB,
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=10)

        # 모드
        mc = self._card(self)
        self.mode = ctk.CTkSegmentedButton(
            mc, values=["취소표 감지", "상영 오픈 감지"], selected_color=RED,
            selected_hover_color=RED_DK, font=ctk.CTkFont(size=13, weight="bold"),
            command=lambda _v: None)
        self.mode.set("취소표 감지")
        self.mode.pack(fill="x", padx=16, pady=12)

        # 대상
        tc = self._card(self, "대상 선택")
        grid = ctk.CTkFrame(tc, fg_color=PANEL)
        grid.pack(fill="x", padx=16, pady=(4, 12))
        for i in range(4):
            grid.grid_columnconfigure(i, weight=1)

        def lab(r, cc, t):
            ctk.CTkLabel(grid, text=t, text_color=SUB, font=ctk.CTkFont(size=11)).grid(
                row=r, column=cc, sticky="w", padx=6, pady=(6, 0))

        self.dd_movie = ctk.CTkOptionMenu(grid, values=["불러오는 중…"], fg_color="#2A2A30",
                                          button_color=RED, button_hover_color=RED_DK)
        self.dd_theater = ctk.CTkOptionMenu(grid, values=["불러오는 중…"], fg_color="#2A2A30",
                                            button_color=RED, button_hover_color=RED_DK)
        self.dd_date = ctk.CTkOptionMenu(grid, values=[l for l, _ in self.dates], fg_color="#2A2A30",
                                         button_color=RED, button_hover_color=RED_DK)
        self.dd_screen = ctk.CTkOptionMenu(grid, values=SCREENS, fg_color="#2A2A30",
                                           button_color=RED, button_hover_color=RED_DK)
        lab(0, 0, "영화"); self.dd_movie.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
        lab(0, 1, "극장"); self.dd_theater.grid(row=1, column=1, sticky="ew", padx=6, pady=(0, 6))
        lab(0, 2, "날짜"); self.dd_date.grid(row=1, column=2, sticky="ew", padx=6, pady=(0, 6))
        lab(0, 3, "상영관"); self.dd_screen.grid(row=1, column=3, sticky="ew", padx=6, pady=(0, 6))

        lab(2, 0, "회차(시간)")
        self.dd_time = ctk.CTkOptionMenu(grid, values=["전체(자동)"], fg_color="#2A2A30",
                                         button_color=RED, button_hover_color=RED_DK)
        self.dd_time.grid(row=3, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 6))
        ctk.CTkButton(grid, text="회차 불러오기", fg_color="#33333A", hover_color="#44444C",
                      command=self._load_times).grid(row=3, column=2, sticky="ew", padx=6, pady=(0, 6))

        # 인원 / 좌석
        pc = self._card(self, "인원 · 좌석")
        prow = ctk.CTkFrame(pc, fg_color=PANEL); prow.pack(fill="x", padx=16, pady=(4, 4))
        self.n_gen = ctk.CTkOptionMenu(prow, values=[str(i) for i in range(0, 9)], width=64,
                                       fg_color="#2A2A30", button_color=RED, button_hover_color=RED_DK)
        self.n_teen = ctk.CTkOptionMenu(prow, values=[str(i) for i in range(0, 9)], width=64,
                                        fg_color="#2A2A30", button_color=RED, button_hover_color=RED_DK)
        self.n_pref = ctk.CTkOptionMenu(prow, values=[str(i) for i in range(0, 9)], width=64,
                                        fg_color="#2A2A30", button_color=RED, button_hover_color=RED_DK)
        self.n_gen.set("2")
        for t, w in [("일반", self.n_gen), ("청소년", self.n_teen), ("우대", self.n_pref)]:
            ctk.CTkLabel(prow, text=t, text_color=SUB).pack(side="left", padx=(10, 3)); w.pack(side="left")

        srow = ctk.CTkFrame(pc, fg_color=PANEL); srow.pack(fill="x", padx=16, pady=(4, 12))
        ctk.CTkLabel(srow, text="원하는 좌석", text_color=SUB).pack(side="left", padx=(6, 4))
        self.e_seats = ctk.CTkEntry(srow, placeholder_text="예: E9,E10 (비우면 자동)", width=170,
                                    fg_color="#2A2A30")
        self.e_seats.pack(side="left")
        self.sw_only = ctk.CTkSwitch(srow, text="이 좌석만", progress_color=RED)
        self.sw_only.pack(side="left", padx=10)
        ctk.CTkLabel(srow, text="위치", text_color=SUB).pack(side="left", padx=(10, 3))
        self.dd_pos = ctk.CTkOptionMenu(srow, values=["center", "front", "back", "any"], width=90,
                                        fg_color="#2A2A30", button_color=RED, button_hover_color=RED_DK)
        self.dd_pos.pack(side="left")
        ctk.CTkLabel(srow, text="주기(초)", text_color=SUB).pack(side="left", padx=(10, 3))
        self.dd_int = ctk.CTkOptionMenu(srow, values=["5", "10", "15", "30", "60"], width=70,
                                        fg_color="#2A2A30", button_color=RED, button_hover_color=RED_DK)
        self.dd_int.set("10"); self.dd_int.pack(side="left")

        # 디스코드
        dc = self._card(self, "디스코드 알림 (선택)")
        drow = ctk.CTkFrame(dc, fg_color=PANEL); drow.pack(fill="x", padx=16, pady=(4, 12))
        self.e_hook = ctk.CTkEntry(drow, placeholder_text="웹훅 URL", width=430, fg_color="#2A2A30")
        self.e_hook.pack(side="left", padx=(6, 6))
        self.e_ment = ctk.CTkEntry(drow, placeholder_text="멘션 ID", width=140, fg_color="#2A2A30")
        self.e_ment.pack(side="left")

        # 녹화 + 감시
        ac = self._card(self, "녹화 · 감시")
        r1 = ctk.CTkFrame(ac, fg_color=PANEL); r1.pack(fill="x", padx=16, pady=(4, 4))
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

        r2 = ctk.CTkFrame(ac, fg_color=PANEL); r2.pack(fill="x", padx=16, pady=(4, 12))
        ctk.CTkButton(r2, text="설정 저장", fg_color="#33333A", hover_color="#44444C", width=90,
                      command=self._save).pack(side="left")
        self.b_start = ctk.CTkButton(r2, text="감시 시작", fg_color=RED, hover_color=RED_DK,
                                     font=ctk.CTkFont(size=13, weight="bold"), width=130, command=self._watch_start)
        self.b_start.pack(side="left", padx=8)
        self.b_stop = ctk.CTkButton(r2, text="중지", fg_color="#33333A", hover_color="#44444C", width=80,
                                    state="disabled", command=self._watch_stop)
        self.b_stop.pack(side="left")
        self.stat = ctk.CTkLabel(r2, text="● 대기", text_color=SUB); self.stat.pack(side="right")

        # 로그
        self.logbox = ctk.CTkTextbox(self, fg_color="#050506", text_color="#D6D6DA",
                                     font=ctk.CTkFont(size=12), height=150, corner_radius=12)
        self.logbox.pack(fill="both", expand=True, padx=16, pady=(2, 14))
        self.logbox.configure(state="disabled")

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
        self.dd_movie.configure(values=mv)
        self.dd_theater.configure(values=th)
        cfg = load_cfg()
        self.dd_movie.set(cfg.get("movie") if cfg.get("movie") in mv else mv[0])
        self.dd_theater.set(cfg.get("theater") if cfg.get("theater") in th else
                            ("센텀시티" if "센텀시티" in th else th[0]))

    def _load_times(self):
        def worker():
            try:
                mv = self.movies.get(self.dd_movie.get())
                th = self.theaters.get(self.dd_theater.get())
                day = self.date_map.get(self.dd_date.get())
                if not (mv and th and day):
                    self._put("영화/극장/날짜를 먼저 선택하세요."); return
                shows = cgv_api.fetch_showtimes(mv, th[0], day.replace("-", ""))
                labels = ["전체(자동)"]
                self.time_map = {}
                for s in shows:
                    lb = f"{s.time}  {s.screen} 잔여{s.remaining}"
                    labels.append(lb); self.time_map[lb] = s.time
                self.after(0, lambda: (self.dd_time.configure(values=labels), self.dd_time.set(labels[0])))
                self._put(f"회차 {len(shows)}건 불러옴.")
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
        if self.watch_thread and self.watch_thread.is_alive():
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

    # ---------- 설정 ----------
    def _target(self):
        return {
            "mode": self.mode.get(),
            "movie": self.dd_movie.get(),
            "theater": self.dd_theater.get(),
            "date": self.date_map.get(self.dd_date.get(), ""),
            "date_label": self.dd_date.get(),
            "time": self.time_map.get(self.dd_time.get(), ""),
            "time_label": self.dd_time.get(),
            "screen_type": "" if self.dd_screen.get() == "전체" else self.dd_screen.get(),
            "persons": {"일반": int(self.n_gen.get()), "청소년": int(self.n_teen.get()), "우대": int(self.n_pref.get())},
            "preferred": [s.strip() for s in self.e_seats.get().split(",") if s.strip()],
            "only_preferred": bool(self.sw_only.get()),
            "prefer": self.dd_pos.get(),
            "interval": int(self.dd_int.get()),
            "webhook": self.e_hook.get().strip(),
            "mention": self.e_ment.get().strip(),
        }

    def _save(self):
        save_cfg(self._target()); self._put("설정 저장됨.")

    def _load(self):
        c = load_cfg()
        if not c:
            return
        if c.get("mode"):
            self.mode.set(c["mode"])
        if c.get("date_label") in self.date_map:
            self.dd_date.set(c["date_label"])
        self.dd_screen.set(c.get("screen_type") or "전체")
        p = c.get("persons", {})
        self.n_gen.set(str(p.get("일반", 2))); self.n_teen.set(str(p.get("청소년", 0))); self.n_pref.set(str(p.get("우대", 0)))
        if c.get("preferred"):
            self.e_seats.insert(0, ",".join(c["preferred"]))
        if c.get("only_preferred"):
            self.sw_only.select()
        self.dd_pos.set(c.get("prefer", "center"))
        self.dd_int.set(str(c.get("interval", 10)))
        if c.get("webhook"):
            self.e_hook.insert(0, c["webhook"])
        if c.get("mention"):
            self.e_ment.insert(0, c["mention"])

    # ---------- 감시 ----------
    def _watch_start(self):
        if self.watch_thread and self.watch_thread.is_alive():
            return
        if not os.path.exists(RECIPE):
            self._put("먼저 '녹화 시작하기'로 예매 흐름을 1회 녹화하세요."); return
        t = self._target()
        if not t["date"]:
            self._put("날짜를 선택하세요."); return
        if t["mode"] == "취소표 감지" and not t["time"]:
            self._put("취소표 감지는 '회차(시간)'를 선택해야 합니다. (회차 불러오기 후 선택)"); return
        save_cfg(t)
        recipe = json.load(open(RECIPE, encoding="utf-8"))
        notifier = None
        if t["webhook"]:
            from cgv_macro.notifier import DiscordNotifier
            notifier = DiscordNotifier(t["webhook"], t["mention"])
        from cgv_macro.watcher import Watcher
        self.watcher = Watcher(recipe, t, notifier)
        self.watch_stop.clear()

        def worker():
            try:
                self.watcher.run(self.watch_stop, log=self._put)
            except Exception as e:  # noqa: BLE001
                self._put(f"[감시] 오류: {e}")
            finally:
                self.after(0, self._watch_ended)
        self.watch_thread = threading.Thread(target=worker, daemon=True); self.watch_thread.start()
        self.b_start.configure(state="disabled")
        self.b_stop.configure(state="normal", fg_color=RED, hover_color=RED_DK)
        self.stat.configure(text="● 감시 중", text_color=GREEN)

    def _watch_stop(self):
        self.watch_stop.set(); self.stat.configure(text="● 중지 중…", text_color="#E0A030")

    def _watch_ended(self):
        if self.watcher:
            self.watcher.close()
        self.b_start.configure(state="normal")
        self.b_stop.configure(state="disabled", fg_color="#33333A")
        self.stat.configure(text="● 대기", text_color=SUB)

    def _on_close(self):
        self.watch_stop.set(); self.rec_stop.set()
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
