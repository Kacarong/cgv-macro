"""
CGV 상영 오픈/취소표 감시기 — GUI (Tkinter).

무인증 CGV API 를 폴링해 상영 오픈/잔여석/취소표를 디스코드로 알린다.
로그인·브라우저 불필요. exe 로 패키징 가능(build_exe.bat).
"""
from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from cgv_macro.config import load_config, save_config_dict, load_raw, ConfigError
from cgv_macro.logger import setup_logger
from cgv_macro import paths

CONFIG_PATH = paths.config_path()  # 실행 위치와 무관한 고정 폴더(재빌드해도 유지)
TARGET_COLS = ("name", "movie", "theater", "date", "time", "screen")


# ----------------------- 순수 로직(테스트 가능) -----------------------
def build_config_dict(targets: list[dict], settings: dict) -> dict:
    return {
        "targets": targets,
        "poll": {
            "interval_seconds": settings["interval"],
            "jitter_seconds": settings["jitter"],
        },
        "alerts": {
            "on_showtime_open": settings["a_open"],
            "on_seats_available": settings["a_avail"],
            "on_soldout_to_available": settings["a_cancel"],
            "min_remaining_seats": settings["min_seats"],
        },
        "discord": {
            "webhook_url": settings["webhook"],
            "mention": settings["mention"],
        },
        "logging": {"dir": settings.get("logs_dir", "./logs"), "level": "INFO"},
    }


# ----------------------- 로그 → GUI 핸들러 -----------------------
class QueueLogHandler(logging.Handler):
    def __init__(self, q: "queue.Queue[str]") -> None:
        super().__init__()
        self.q = q

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.q.put_nowait(self.format(record))
        except Exception:  # noqa: BLE001
            pass


# ----------------------- 대상 추가/수정 다이얼로그 -----------------------
class TargetDialog(tk.Toplevel):
    FIELDS = [
        ("name", "별칭", ""),
        ("movie", "영화명", ""),
        ("movie_code", "영화코드 movNo(선택)", ""),
        ("theater", "극장명", ""),
        ("theater_code", "극장코드 siteNo(선택)", ""),
        ("date", "날짜(YYYY-MM-DD)", ""),
        ("time_from", "시작시간(HH:MM, 비우면 하루전체)", "00:00"),
        ("time_to", "종료시간(HH:MM, 비우면 하루전체)", "23:59"),
        ("screen_type", "상영관/포맷 필터(선택, 예 IMAX/4DX)", ""),
    ]

    def __init__(self, master, initial: dict | None = None) -> None:
        super().__init__(master)
        self.title("감시 대상")
        self.result: dict | None = None
        self.vars: dict[str, tk.StringVar] = {}
        initial = initial or {}
        for i, (key, label, default) in enumerate(self.FIELDS):
            ttk.Label(self, text=label).grid(row=i, column=0, sticky="e", padx=6, pady=3)
            var = tk.StringVar(value=str(initial.get(key, default)))
            ttk.Entry(self, textvariable=var, width=42).grid(row=i, column=1, padx=6, pady=3)
            self.vars[key] = var
        btns = ttk.Frame(self)
        btns.grid(row=len(self.FIELDS), column=0, columnspan=2, pady=8)
        ttk.Button(btns, text="확인", command=self._ok).pack(side="left", padx=6)
        ttk.Button(btns, text="취소", command=self.destroy).pack(side="left", padx=6)
        self.transient(master)
        self.grab_set()

    def _ok(self) -> None:
        d = {k: v.get().strip() for k, v in self.vars.items()}
        if not (d["movie"] or d["movie_code"]):
            messagebox.showerror("입력 오류", "영화명 또는 영화코드가 필요합니다.")
            return
        if not (d["theater"] or d["theater_code"]):
            messagebox.showerror("입력 오류", "극장명 또는 극장코드가 필요합니다.")
            return
        if not d["date"]:
            messagebox.showerror("입력 오류", "날짜가 필요합니다.")
            return
        if not d["name"]:
            d["name"] = d["movie"] or d["movie_code"]
        if not d["time_from"]:
            d["time_from"] = "00:00"
        if not d["time_to"]:
            d["time_to"] = "23:59"
        self.result = d
        self.destroy()


# ----------------------- 메인 앱 -----------------------
class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("CGV 상영 오픈/취소표 감시기")
        self.geometry("820x680")
        self.targets: list[dict] = []
        self.log_q: "queue.Queue[str]" = queue.Queue()
        self.monitor_thread: threading.Thread | None = None
        self.stop_event = threading.Event()

        self._build_ui()
        self._load_existing()
        self._attach_logging()
        self.after(200, self._drain_logs)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        pad = {"padx": 6, "pady": 4}

        tf = ttk.LabelFrame(self, text="감시 대상")
        tf.pack(fill="x", **pad)
        self.tree = ttk.Treeview(tf, columns=TARGET_COLS, show="headings", height=5)
        heads = {"name": "별칭", "movie": "영화", "theater": "극장",
                 "date": "날짜", "time": "시간대", "screen": "상영관"}
        for c in TARGET_COLS:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=130 if c in ("name", "movie", "theater") else 90)
        self.tree.pack(side="left", fill="x", expand=True, padx=6, pady=6)
        tb = ttk.Frame(tf)
        tb.pack(side="left", padx=6)
        ttk.Button(tb, text="추가", command=self._add_target).pack(fill="x", pady=2)
        ttk.Button(tb, text="수정", command=self._edit_target).pack(fill="x", pady=2)
        ttk.Button(tb, text="삭제", command=self._del_target).pack(fill="x", pady=2)

        sf = ttk.LabelFrame(self, text="설정")
        sf.pack(fill="x", **pad)
        self.v_interval = tk.IntVar(value=45)
        self.v_jitter = tk.IntVar(value=15)
        self.v_min = tk.IntVar(value=1)
        self.v_open = tk.BooleanVar(value=True)
        self.v_avail = tk.BooleanVar(value=True)
        self.v_cancel = tk.BooleanVar(value=True)
        self.v_webhook = tk.StringVar(value="")
        self.v_mention = tk.StringVar(value="")

        r = 0
        ttk.Label(sf, text="폴링 주기(초)").grid(row=r, column=0, sticky="e", **pad)
        ttk.Spinbox(sf, from_=5, to=3600, textvariable=self.v_interval, width=8).grid(row=r, column=1, sticky="w", **pad)
        ttk.Label(sf, text="지터(초)").grid(row=r, column=2, sticky="e", **pad)
        ttk.Spinbox(sf, from_=0, to=120, textvariable=self.v_jitter, width=8).grid(row=r, column=3, sticky="w", **pad)
        r += 1
        ttk.Checkbutton(sf, text="상영 오픈 알림", variable=self.v_open).grid(row=r, column=0, sticky="w", **pad)
        ttk.Checkbutton(sf, text="잔여석 알림", variable=self.v_avail).grid(row=r, column=1, sticky="w", **pad)
        ttk.Checkbutton(sf, text="취소표(매진→잔여) 알림", variable=self.v_cancel).grid(row=r, column=2, columnspan=2, sticky="w", **pad)
        r += 1
        ttk.Label(sf, text="최소 잔여석").grid(row=r, column=0, sticky="e", **pad)
        ttk.Spinbox(sf, from_=1, to=500, textvariable=self.v_min, width=8).grid(row=r, column=1, sticky="w", **pad)
        r += 1
        ttk.Label(sf, text="디스코드 웹훅 URL").grid(row=r, column=0, sticky="e", **pad)
        ttk.Entry(sf, textvariable=self.v_webhook, width=62).grid(row=r, column=1, columnspan=4, sticky="w", **pad)
        r += 1
        ttk.Label(sf, text="멘션(선택)").grid(row=r, column=0, sticky="e", **pad)
        ttk.Entry(sf, textvariable=self.v_mention, width=30).grid(row=r, column=1, columnspan=3, sticky="w", **pad)

        bf = ttk.Frame(self)
        bf.pack(fill="x", **pad)
        ttk.Button(bf, text="설정 저장", command=self._save).pack(side="left", padx=4)
        ttk.Button(bf, text="설정 불러오기", command=self._reload).pack(side="left", padx=4)
        ttk.Button(bf, text="디스코드 테스트", command=self._test_discord).pack(side="left", padx=4)
        self.btn_start = ttk.Button(bf, text="감시 시작", command=self._start)
        self.btn_start.pack(side="left", padx=4)
        self.btn_stop = ttk.Button(bf, text="중지", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=4)
        self.status = ttk.Label(bf, text="● 대기", foreground="gray")
        self.status.pack(side="right", padx=8)

        lf = ttk.LabelFrame(self, text="로그")
        lf.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(lf, height=14, state="disabled", wrap="none")
        self.log_text.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(lf, command=self.log_text.yview)
        sb.pack(side="right", fill="y")
        self.log_text.config(yscrollcommand=sb.set)

    # ---------- 대상 관리 ----------
    def _refresh_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for t in self.targets:
            self.tree.insert("", "end", values=(
                t.get("name", ""), t.get("movie", "") or t.get("movie_code", ""),
                t.get("theater", "") or t.get("theater_code", ""), t.get("date", ""),
                f"{t.get('time_from','')}-{t.get('time_to','')}", t.get("screen_type", ""),
            ))

    def _add_target(self) -> None:
        dlg = TargetDialog(self)
        self.wait_window(dlg)
        if dlg.result:
            self.targets.append(dlg.result)
            self._refresh_tree()

    def _selected_index(self) -> int | None:
        sel = self.tree.selection()
        return self.tree.index(sel[0]) if sel else None

    def _edit_target(self) -> None:
        idx = self._selected_index()
        if idx is None:
            messagebox.showinfo("안내", "수정할 대상을 선택하세요.")
            return
        dlg = TargetDialog(self, self.targets[idx])
        self.wait_window(dlg)
        if dlg.result:
            self.targets[idx] = dlg.result
            self._refresh_tree()

    def _del_target(self) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        del self.targets[idx]
        self._refresh_tree()

    # ---------- 설정 ----------
    def _collect_settings(self) -> dict:
        return {
            "interval": max(5, self.v_interval.get()),
            "jitter": self.v_jitter.get(),
            "min_seats": self.v_min.get(),
            "a_open": self.v_open.get(),
            "a_avail": self.v_avail.get(),
            "a_cancel": self.v_cancel.get(),
            "webhook": self.v_webhook.get().strip(),
            "mention": self.v_mention.get().strip(),
            "logs_dir": paths.logs_dir(),
        }

    def _save(self) -> bool:
        if not self.targets:
            messagebox.showerror("저장 실패", "감시 대상을 최소 1개 추가하세요.")
            return False
        if not self.v_webhook.get().strip():
            messagebox.showerror("저장 실패", "디스코드 웹훅 URL 을 입력하세요.")
            return False
        data = build_config_dict(self.targets, self._collect_settings())
        try:
            save_config_dict(CONFIG_PATH, data)
            load_config(CONFIG_PATH)
        except ConfigError as e:
            messagebox.showerror("설정 오류", str(e))
            return False
        self._log(f"설정 저장 완료 → {CONFIG_PATH}")
        return True

    def _reload(self) -> None:
        import os
        if not os.path.exists(CONFIG_PATH):
            messagebox.showinfo("안내", "저장된 설정(config.yaml)이 없습니다. 먼저 '설정 저장'을 하세요.")
            return
        self._load_existing()
        self._log(f"설정을 불러왔습니다 ← {CONFIG_PATH}")

    def _load_existing(self) -> None:
        data = load_raw(CONFIG_PATH)
        if not data:
            return
        self.targets = list(data.get("targets") or [])
        self._refresh_tree()
        poll = data.get("poll", {})
        al = data.get("alerts", {})
        dc = data.get("discord", {})
        self.v_interval.set(poll.get("interval_seconds", 45))
        self.v_jitter.set(poll.get("jitter_seconds", 15))
        self.v_min.set(al.get("min_remaining_seats", 1))
        self.v_open.set(al.get("on_showtime_open", True))
        self.v_avail.set(al.get("on_seats_available", True))
        self.v_cancel.set(al.get("on_soldout_to_available", True))
        self.v_webhook.set(dc.get("webhook_url", ""))
        self.v_mention.set(dc.get("mention", ""))

    # ---------- 로깅 ----------
    def _attach_logging(self) -> None:
        logger = setup_logger(paths.logs_dir(), "INFO")
        h = QueueLogHandler(self.log_q)
        h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
        logger.addHandler(h)
        self.logger = logger

    def _log(self, msg: str) -> None:
        self.log_q.put_nowait(msg)

    def _drain_logs(self) -> None:
        try:
            while True:
                line = self.log_q.get_nowait()
                self.log_text.config(state="normal")
                self.log_text.insert("end", line + "\n")
                self.log_text.see("end")
                self.log_text.config(state="disabled")
        except queue.Empty:
            pass
        self.after(300, self._drain_logs)

    # ---------- 디스코드 테스트 ----------
    def _test_discord(self) -> None:
        url = self.v_webhook.get().strip()
        if not url:
            messagebox.showerror("오류", "웹훅 URL 을 먼저 입력하세요.")
            return
        from cgv_macro.notifier import DiscordNotifier
        n = DiscordNotifier(url, self.v_mention.get().strip())
        ok = n.notify_error("테스트 메시지입니다. 웹훅 정상 동작.", cooldown_seconds=0)
        self._log("디스코드 테스트: " + ("성공" if ok else "실패(URL 확인)"))

    # ---------- 감시 시작/중지 ----------
    def _start(self) -> None:
        if self.monitor_thread and self.monitor_thread.is_alive():
            return
        if not self._save():
            return
        try:
            cfg = load_config(CONFIG_PATH)
        except ConfigError as e:
            messagebox.showerror("설정 오류", str(e))
            return
        self.stop_event.clear()
        from cgv_macro.monitor import run

        def worker():
            try:
                run(cfg, stop_event=self.stop_event)
            except Exception as e:  # noqa: BLE001
                self._log(f"감시 스레드 오류: {e}")
            finally:
                self.after(0, self._on_monitor_end)

        self.monitor_thread = threading.Thread(target=worker, daemon=True)
        self.monitor_thread.start()
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.status.config(text="● 감시 중", foreground="green")
        self._log("감시를 시작했습니다.")

    def _stop(self) -> None:
        self.stop_event.set()
        self.status.config(text="● 중지 중...", foreground="orange")
        self._log("중지 요청을 보냈습니다.")

    def _on_monitor_end(self) -> None:
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.status.config(text="● 대기", foreground="gray")

    def _on_close(self) -> None:
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.stop_event.set()
        self.destroy()


def main() -> int:
    App().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
