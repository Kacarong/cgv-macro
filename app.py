"""
CGV 취소표/오픈 감시 + 좌석 자동잡기 — GUI (깔끔 버전).

한 창에서: (1) 최초 1회 녹화, (2) 대상/좌석/인원 설정, (3) 감시 시작.
감시가 취소표/오픈을 감지하면 녹화한 흐름대로 좌석을 잡고 결제 페이지까지 진입.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from cgv_macro import paths
from cgv_macro.logger import setup_logger

RED = "#D93832"
GREEN = "#ADEF9F"
INK = "#222222"
GRAY = "#F4F4F5"

CONFIG = os.path.join(paths.data_dir(), "app_config.json")
RECIPE = os.path.join(paths.data_dir(), "recipe.json")


def load_cfg() -> dict:
    if os.path.exists(CONFIG):
        try:
            return json.load(open(CONFIG, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def save_cfg(d: dict) -> None:
    json.dump(d, open(CONFIG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("CGV 좌석 감시·자동잡기")
        self.geometry("760x780")
        self.configure(bg="white")
        self.log_q: "queue.Queue[str]" = queue.Queue()
        self.rec_thread = None
        self.rec_start = threading.Event()
        self.rec_stop = threading.Event()
        self.watch_thread = None
        self.watch_stop = threading.Event()
        self.watcher = None

        self._build()
        self._load()
        self.logger = setup_logger(paths.logs_dir(), "INFO")
        self.after(200, self._drain)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- UI ----------
    def _hdr(self, parent, text):
        f = tk.Frame(parent, bg="white")
        tk.Label(f, text=text, bg="white", fg=INK, font=("맑은 고딕", 11, "bold")).pack(anchor="w")
        return f

    def _build(self) -> None:
        pad = {"padx": 12, "pady": 6}
        top = tk.Frame(self, bg=RED, height=52)
        top.pack(fill="x")
        tk.Label(top, text="  CGV 좌석 감시 · 자동잡기", bg=RED, fg="white",
                 font=("맑은 고딕", 14, "bold")).pack(side="left", pady=10)

        body = tk.Frame(self, bg="white")
        body.pack(fill="both", expand=True, padx=10, pady=6)

        # 1) 녹화
        self._hdr(body, "1) 최초 1회 녹화 (로그인 + 예매 클릭 순서 기록)").pack(fill="x", **pad)
        rf = tk.Frame(body, bg="white")
        rf.pack(fill="x", **pad)
        self.rec_status = tk.Label(rf, text="", bg="white", fg="#666")
        self.btn_rec = tk.Button(rf, text="녹화 시작하기", bg=RED, fg="white", relief="flat",
                                 font=("맑은 고딕", 10, "bold"), command=self._rec_open, width=14)
        self.btn_rec.pack(side="left")
        self.btn_rec_go = tk.Button(rf, text="① 로그인함·녹화시작", bg=GREEN, fg=INK, relief="flat",
                                    state="disabled", command=lambda: self.rec_start.set(), width=18)
        self.btn_rec_go.pack(side="left", padx=6)
        self.btn_rec_end = tk.Button(rf, text="② 녹화끝(저장)", bg="#DDDDDD", fg=INK, relief="flat",
                                     state="disabled", command=lambda: self.rec_stop.set(), width=14)
        self.btn_rec_end.pack(side="left")
        self.rec_status.pack(side="left", padx=10)

        # 2) 대상 설정
        self._hdr(body, "2) 대상 설정").pack(fill="x", **pad)
        sf = tk.Frame(body, bg="white")
        sf.pack(fill="x", **pad)
        self.v_movie = tk.StringVar(value="오디세이")
        self.v_theater = tk.StringVar(value="센텀시티")
        self.v_date = tk.StringVar(value="")
        self.v_time = tk.StringVar(value="")
        self.v_screen = tk.StringVar(value="")
        self.v_gen = tk.IntVar(value=2)
        self.v_teen = tk.IntVar(value=0)
        self.v_pref = tk.IntVar(value=0)
        self.v_seats = tk.StringVar(value="")
        self.v_only = tk.BooleanVar(value=False)
        self.v_pos = tk.StringVar(value="center")
        self.v_interval = tk.IntVar(value=10)

        def row(r, label, widget):
            tk.Label(sf, text=label, bg="white", fg=INK).grid(row=r, column=0, sticky="e", padx=6, pady=4)
            widget.grid(row=r, column=1, sticky="w", padx=6, pady=4)

        row(0, "영화명", tk.Entry(sf, textvariable=self.v_movie, width=24))
        row(1, "극장명", tk.Entry(sf, textvariable=self.v_theater, width=24))
        row(2, "날짜(YYYY-MM-DD)", tk.Entry(sf, textvariable=self.v_date, width=24))
        row(3, "회차 시간(HH:MM, 비우면 전체)", tk.Entry(sf, textvariable=self.v_time, width=24))
        row(4, "상영관 필터(선택, 예 IMAX)", tk.Entry(sf, textvariable=self.v_screen, width=24))
        pf = tk.Frame(sf, bg="white")
        tk.Label(pf, text="일반", bg="white").pack(side="left")
        tk.Spinbox(pf, from_=0, to=8, textvariable=self.v_gen, width=3).pack(side="left", padx=(2, 8))
        tk.Label(pf, text="청소년", bg="white").pack(side="left")
        tk.Spinbox(pf, from_=0, to=8, textvariable=self.v_teen, width=3).pack(side="left", padx=(2, 8))
        tk.Label(pf, text="우대", bg="white").pack(side="left")
        tk.Spinbox(pf, from_=0, to=8, textvariable=self.v_pref, width=3).pack(side="left", padx=2)
        row(5, "인원", pf)
        row(6, "원하는 좌석(쉼표, 예 E9,E10)", tk.Entry(sf, textvariable=self.v_seats, width=24))
        of = tk.Frame(sf, bg="white")
        tk.Checkbutton(of, text="원하는 좌석만(뜰 때까지 대기)", variable=self.v_only, bg="white").pack(side="left")
        row(7, "", of)
        posf = tk.Frame(sf, bg="white")
        ttk.Combobox(posf, textvariable=self.v_pos, values=["center", "front", "back", "any"],
                     width=8, state="readonly").pack(side="left")
        tk.Label(posf, text="   폴링주기(초)", bg="white").pack(side="left")
        tk.Spinbox(posf, from_=5, to=120, textvariable=self.v_interval, width=4).pack(side="left", padx=4)
        row(8, "좌석위치 / 주기", posf)

        # 디스코드(선택)
        self._hdr(body, "디스코드 알림(선택)").pack(fill="x", **pad)
        df = tk.Frame(body, bg="white")
        df.pack(fill="x", **pad)
        self.v_webhook = tk.StringVar(value="")
        self.v_mention = tk.StringVar(value="")
        tk.Label(df, text="웹훅 URL", bg="white").grid(row=0, column=0, sticky="e", padx=6)
        tk.Entry(df, textvariable=self.v_webhook, width=52).grid(row=0, column=1, sticky="w", padx=6)
        tk.Label(df, text="멘션(ID)", bg="white").grid(row=1, column=0, sticky="e", padx=6)
        tk.Entry(df, textvariable=self.v_mention, width=24).grid(row=1, column=1, sticky="w", padx=6)

        # 3) 감시
        bf = tk.Frame(body, bg="white")
        bf.pack(fill="x", **pad)
        tk.Button(bf, text="설정 저장", bg="#EEEEEE", fg=INK, relief="flat",
                  command=self._save).pack(side="left")
        self.btn_start = tk.Button(bf, text="감시 시작", bg=RED, fg="white", relief="flat",
                                   font=("맑은 고딕", 10, "bold"), width=12, command=self._watch_start)
        self.btn_start.pack(side="left", padx=8)
        self.btn_stop = tk.Button(bf, text="중지", bg="#DDDDDD", fg=INK, relief="flat",
                                  width=8, state="disabled", command=self._watch_stop)
        self.btn_stop.pack(side="left")
        self.status = tk.Label(bf, text="● 대기", bg="white", fg="#888")
        self.status.pack(side="right")

        # 로그
        lf = tk.Frame(body, bg="white")
        lf.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(lf, height=12, bg="#0E0E0E", fg="#EEEEEE", relief="flat", wrap="word")
        self.log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(lf, command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.config(yscrollcommand=sb.set, state="disabled")

    # ---------- 로그 ----------
    def _put(self, msg: str) -> None:
        self.log_q.put_nowait(str(msg))

    def _drain(self) -> None:
        try:
            while True:
                line = self.log_q.get_nowait()
                self.log.config(state="normal")
                self.log.insert("end", line + "\n")
                self.log.see("end")
                self.log.config(state="disabled")
        except queue.Empty:
            pass
        # 녹화 파일 상태 표시
        self.rec_status.config(text=("녹화됨 ✓" if os.path.exists(RECIPE) else "녹화 없음"),
                               fg=("#2a7d2a" if os.path.exists(RECIPE) else "#b00"))
        self.after(300, self._drain)

    # ---------- 녹화 ----------
    def _rec_open(self) -> None:
        if self.watch_thread and self.watch_thread.is_alive():
            messagebox.showinfo("안내", "감시 중에는 녹화할 수 없습니다."); return
        from cgv_macro.recorder import run_recorder
        self.rec_start.clear(); self.rec_stop.clear()
        self.btn_rec.config(state="disabled")
        self.btn_rec_go.config(state="normal")
        self.btn_rec_end.config(state="normal", bg=RED, fg="white")

        def worker():
            try:
                run_recorder(self.rec_start, self.rec_stop, self._put)
            except Exception as e:  # noqa: BLE001
                self._put(f"[녹화] 오류: {e}")
            finally:
                self.after(0, self._rec_reset)
        self.rec_thread = threading.Thread(target=worker, daemon=True)
        self.rec_thread.start()
        self._put("[녹화] 크롬에서 로그인 후 '① 로그인함·녹화시작'을 누르세요.")

    def _rec_reset(self) -> None:
        self.btn_rec.config(state="normal")
        self.btn_rec_go.config(state="disabled")
        self.btn_rec_end.config(state="disabled", bg="#DDDDDD", fg=INK)

    # ---------- 설정 저장/로드 ----------
    def _target(self) -> dict:
        return {
            "movie": self.v_movie.get().strip(),
            "theater": self.v_theater.get().strip(),
            "date": self.v_date.get().strip(),
            "time": self.v_time.get().strip(),
            "screen_type": self.v_screen.get().strip(),
            "persons": {"일반": self.v_gen.get(), "청소년": self.v_teen.get(), "우대": self.v_pref.get()},
            "preferred": [s.strip() for s in self.v_seats.get().split(",") if s.strip()],
            "only_preferred": self.v_only.get(),
            "prefer": self.v_pos.get(),
            "interval": self.v_interval.get(),
            "webhook": self.v_webhook.get().strip(),
            "mention": self.v_mention.get().strip(),
        }

    def _save(self) -> None:
        save_cfg(self._target())
        self._put("설정 저장됨.")

    def _load(self) -> None:
        c = load_cfg()
        if not c:
            return
        self.v_movie.set(c.get("movie", "오디세이"))
        self.v_theater.set(c.get("theater", "센텀시티"))
        self.v_date.set(c.get("date", ""))
        self.v_time.set(c.get("time", ""))
        self.v_screen.set(c.get("screen_type", ""))
        p = c.get("persons", {})
        self.v_gen.set(p.get("일반", 2)); self.v_teen.set(p.get("청소년", 0)); self.v_pref.set(p.get("우대", 0))
        self.v_seats.set(",".join(c.get("preferred", [])))
        self.v_only.set(c.get("only_preferred", False))
        self.v_pos.set(c.get("prefer", "center"))
        self.v_interval.set(c.get("interval", 10))
        self.v_webhook.set(c.get("webhook", ""))
        self.v_mention.set(c.get("mention", ""))

    # ---------- 감시 ----------
    def _watch_start(self) -> None:
        if self.watch_thread and self.watch_thread.is_alive():
            return
        if not os.path.exists(RECIPE):
            messagebox.showerror("녹화 필요", "먼저 '녹화 시작하기'로 예매 흐름을 1회 녹화하세요."); return
        t = self._target()
        if not t["date"] or not any(ch.isdigit() for ch in t["date"]):
            messagebox.showerror("입력 오류", "날짜를 입력하세요(예: 2026-09-08)."); return
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
        self.watch_thread = threading.Thread(target=worker, daemon=True)
        self.watch_thread.start()
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal", bg=RED, fg="white")
        self.status.config(text="● 감시 중", fg="#2a7d2a")

    def _watch_stop(self) -> None:
        self.watch_stop.set()
        self.status.config(text="● 중지 중...", fg="#c60")

    def _watch_ended(self) -> None:
        if self.watcher:
            self.watcher.close()
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled", bg="#DDDDDD", fg=INK)
        self.status.config(text="● 대기", fg="#888")

    def _on_close(self) -> None:
        self.watch_stop.set(); self.rec_stop.set()
        self.destroy()


def main() -> int:
    App().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
