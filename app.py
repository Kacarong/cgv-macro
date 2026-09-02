"""
CGV 좌석 감시 · 자동잡기 — 다크 UI (CustomTkinter).

탭: 취소표 감지 / 상영 오픈 감지 / 무대인사 감지 / 설정.
- '로그인 준비'로 마스터 1회 로그인 → 세션(쿠키)을 파일로 저장(storage_state).
- '감시 시작' 시 대상마다 '독립 크롬 창'을 저장된 세션으로 열어(로그인 공유) 진짜 병렬 감시·선점.
- 각 창은 자기 대상만 감시하다 자리가 나면 그 창에서 결제창까지 선점.
"""
from __future__ import annotations

import datetime
import json
import os
import queue
import threading
import time

import customtkinter as ctk

from cgv_macro import paths, cgv_api
from cgv_macro.logger import setup_logger

RED = "#E03A34"
RED_DK = "#B92C27"
GREEN = "#8FE388"
PANEL = "#1B1B1F"
BG = "#0F0F10"
SUB = "#9A9AA2"

VERSION = "v84"
CONFIG = os.path.join(paths.data_dir(), "app_config.json")
RECIPE = os.path.join(paths.data_dir(), "recipe.json")
STATE_JSON = os.path.join(paths.data_dir(), "cgv_state.json")   # 로그인 세션(창 공유용)
GRABBED_JSON = os.path.join(paths.data_dir(), "grabbed.json")   # 이미 선점한 회차(중복 방지)
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
        self.workers: list = []
        self._active = 0
        self._notifier = None
        self.master_ready = False
        self._logging_in = False
        self.success_q: "queue.Queue" = queue.Queue()
        self.grabbed_keys: set = _load_grabbed()   # 이미 선점한 회차키(중복 예매 방지)

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

        # 구역 지정(선택): 열(A~) 범위 + 번호 범위 안에서만 자동으로 붙여 잡음
        rrow = ctk.CTkFrame(pc, fg_color=PANEL); rrow.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkLabel(rrow, text="구역(선택) 열", text_color=SUB).pack(side="left", padx=(6, 3))
        ns["rf"] = ctk.CTkEntry(rrow, placeholder_text="A", width=46, fg_color="#2A2A30"); ns["rf"].pack(side="left")
        ctk.CTkLabel(rrow, text="~", text_color=SUB).pack(side="left", padx=3)
        ns["rt"] = ctk.CTkEntry(rrow, placeholder_text="H", width=46, fg_color="#2A2A30"); ns["rt"].pack(side="left")
        ctk.CTkLabel(rrow, text="번호", text_color=SUB).pack(side="left", padx=(12, 3))
        ns["nf"] = ctk.CTkEntry(rrow, placeholder_text="10", width=52, fg_color="#2A2A30"); ns["nf"].pack(side="left")
        ctk.CTkLabel(rrow, text="~", text_color=SUB).pack(side="left", padx=3)
        ns["nt"] = ctk.CTkEntry(rrow, placeholder_text="20", width=52, fg_color="#2A2A30"); ns["nt"].pack(side="left")
        ctk.CTkLabel(rrow, text="(비우면 제한 없음 · 예: 열 C~G, 번호 10~20)", text_color=SUB).pack(side="left", padx=8)

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
        # 시간대 필터(회차를 '전체(자동)'로 둘 때, 이 범위 안의 회차만 감시)
        hours = ["전체"] + [f"{h:02d}" for h in range(6, 27)]
        lab(4, 0, "시간대(선택)")
        ns["tfrom"] = self._dd(grid, hours); ns["tfrom"].set("전체")
        ns["tto"] = self._dd(grid, hours); ns["tto"].set("전체")
        ns["tfrom"].grid(row=5, column=0, sticky="ew", padx=6, pady=(0, 8))
        ctk.CTkLabel(grid, text="~ 시", text_color=SUB).grid(row=5, column=1, sticky="w")
        ns["tto"].grid(row=5, column=2, sticky="ew", padx=6, pady=(0, 8))

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
        ctk.CTkLabel(head, text=VERSION, text_color=GREEN,
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left", padx=(0, 6))
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
        drow = ctk.CTkFrame(dc, fg_color=PANEL); drow.pack(fill="x", padx=16, pady=(4, 4))
        self.e_hook = ctk.CTkEntry(drow, placeholder_text="웹훅 URL (채널 알림)", width=430, fg_color="#2A2A30")
        self.e_hook.pack(side="left", padx=(6, 6))
        self.e_ment = ctk.CTkEntry(drow, placeholder_text="멘션 ID", width=140, fg_color="#2A2A30")
        self.e_ment.pack(side="left")
        drow2 = ctk.CTkFrame(dc, fg_color=PANEL); drow2.pack(fill="x", padx=16, pady=(0, 4))
        self.e_bot = ctk.CTkEntry(drow2, placeholder_text="봇 토큰 (DM 알림용)", show="●", width=340, fg_color="#2A2A30")
        self.e_bot.pack(side="left", padx=(6, 6))
        self.e_uid = ctk.CTkEntry(drow2, placeholder_text="내 Discord 유저 ID", width=200, fg_color="#2A2A30")
        self.e_uid.pack(side="left")
        ctk.CTkLabel(dc, text="DM으로 받으려면 봇 토큰 + 내 유저 ID를 넣으세요(둘 다 있으면 DM 우선). 봇과 같은 서버에 있어야\n"
                             "하고 DM 허용이 켜져 있어야 합니다. 채널 알림만 원하면 웹훅 URL만 넣으면 됩니다.",
                     text_color=SUB, wraplength=740, justify="left").pack(anchor="w", padx=16, pady=(0, 12))

        rl = self._card(sett, "자동 재로그인 (세션 만료 시 · 선택 · 기본 꺼짐)")
        rrow = ctk.CTkFrame(rl, fg_color=PANEL); rrow.pack(fill="x", padx=16, pady=(4, 4))
        self.sw_relogin = ctk.CTkSwitch(rrow, text="자동 재로그인 켜기", progress_color=RED)
        self.sw_relogin.pack(side="left")
        rrow2 = ctk.CTkFrame(rl, fg_color=PANEL); rrow2.pack(fill="x", padx=16, pady=(0, 4))
        self.e_cid = ctk.CTkEntry(rrow2, placeholder_text="CGV(CJ ONE) 아이디", width=200, fg_color="#2A2A30")
        self.e_cid.pack(side="left", padx=(0, 6))
        self.e_cpw = ctk.CTkEntry(rrow2, placeholder_text="비밀번호", show="●", width=180, fg_color="#2A2A30")
        self.e_cpw.pack(side="left")
        rrow3 = ctk.CTkFrame(rl, fg_color=PANEL); rrow3.pack(fill="x", padx=16, pady=(0, 8))
        self.e_2cap = ctk.CTkEntry(rrow3, placeholder_text="2Captcha API 키 (있으면 캡챠 자동해석)", width=390, fg_color="#2A2A30")
        self.e_2cap.pack(side="left")
        ctk.CTkLabel(rl, text="아이디/비번은 이 PC(%APPDATA%)에 저장됩니다. 캡챠는 2Captcha 키가 있어야 자동해석돼요"
                             "(없으면 만료 시 '로그인 필요' 알림만).", text_color=SUB, wraplength=740,
                     justify="left").pack(anchor="w", padx=16, pady=(0, 12))

        oc = self._card(sett, "기타")
        orow = ctk.CTkFrame(oc, fg_color=PANEL); orow.pack(fill="x", padx=16, pady=(4, 4))
        ctk.CTkButton(orow, text="설정 내보내기", fg_color="#33333A", hover_color="#44444C",
                      command=self._export_config).pack(side="left")
        ctk.CTkButton(orow, text="설정 불러오기", fg_color="#33333A", hover_color="#44444C",
                      command=self._import_config).pack(side="left", padx=8)
        orow2 = ctk.CTkFrame(oc, fg_color=PANEL); orow2.pack(fill="x", padx=16, pady=(0, 4))
        self.sw_dup = ctk.CTkSwitch(orow2, text="이미 잡은 회차 다시 안 잡기(중복 방지)", progress_color=RED)
        self.sw_dup.pack(side="left")
        orow3 = ctk.CTkFrame(oc, fg_color=PANEL); orow3.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkButton(orow3, text="중복 예매 기록 초기화", fg_color="#33333A", hover_color="#44444C",
                      command=self._clear_grabbed).pack(side="left")
        ctk.CTkLabel(orow3, text="(중복 방지는 기본 꺼짐 — 켰을 때만 이미 잡은 회차를 건너뜀)",
                     text_color=SUB).pack(side="left", padx=10)

        self._note(sett, "사용 순서: (1) '① 로그인 준비'로 크롬에서 1회 로그인(캡챠 포함) → 세션 저장·닫힘. (2) 각 탭에서 대상 추가.\n"
                         "(3) '② 감시 시작' → 대상마다 '독립 크롬 창'이 열려 진짜 병렬로 감시(로그인 공유, 재로그인 없음).\n"
                         "\n"
                         "좌석을 잡으면: 소리 + 프로그램 창이 앞으로 + 팝업 + 디스코드 멘션/결제화면 스샷 알림이 갑니다.\n"
                         "이미 잡은 회차는 재시작해도 다시 안 잡습니다(중복 예매 방지). 시간대 필터로 원하는 시간대 회차만 감시 가능.\n"
                         "요청 과다(429) 시 자동으로 대기 후 재시도합니다.")

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
        try:
            while True:
                info = self.success_q.get_nowait()
                self._alert(info)
        except queue.Empty:
            pass
        self.rec_stat.configure(text=("녹화됨 ✓" if os.path.exists(RECIPE) else "녹화 없음"),
                                text_color=(GREEN if os.path.exists(RECIPE) else RED))
        self.after(300, self._drain)

    # ---------- 성공 알림(소리/앞으로/팝업) ----------
    def _clear_grabbed(self):
        self.grabbed_keys = set()
        _save_grabbed(self.grabbed_keys)
        self._put("중복 예매 기록 초기화됨.")

    # ---------- 설정 내보내기/불러오기(프리셋) ----------
    def _current_cfg(self) -> dict:
        return {
            "cancel": self.cancel_list, "open": self.open_list,
            "event_theaters": self.event_theaters, "days": int(self.dd_days.get()),
            "webhook": self.e_hook.get().strip(), "mention": self.e_ment.get().strip(),
            "bot_token": self.e_bot.get().strip() if hasattr(self, "e_bot") else "",
            "user_id": self.e_uid.get().strip() if hasattr(self, "e_uid") else "",
            "dup_prevent": bool(self.sw_dup.get()) if hasattr(self, "sw_dup") else False,
            "auto_relogin": bool(self.sw_relogin.get()) if hasattr(self, "sw_relogin") else False,
            "cgv_id": self.e_cid.get().strip() if hasattr(self, "e_cid") else "",
            "cgv_pw": self.e_cpw.get() if hasattr(self, "e_cpw") else "",
            "twocaptcha": self.e_2cap.get().strip() if hasattr(self, "e_2cap") else "",
        }

    def _export_config(self):
        try:
            from tkinter import filedialog
            path = filedialog.asksaveasfilename(defaultextension=".json",
                                                filetypes=[("설정 파일", "*.json")],
                                                initialfile="cgv_preset.json")
            if not path:
                return
            json.dump(self._current_cfg(), open(path, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            self._put(f"설정 내보냄: {path}")
        except Exception as e:  # noqa: BLE001
            self._put(f"내보내기 실패: {e}")

    def _import_config(self):
        try:
            from tkinter import filedialog
            path = filedialog.askopenfilename(filetypes=[("설정 파일", "*.json")])
            if not path:
                return
            c = json.load(open(path, encoding="utf-8"))
            self.cancel_list = c.get("cancel", []) or []
            self.open_list = c.get("open", []) or []
            self.event_theaters = c.get("event_theaters", []) or []
            self._refresh_target_list("취소표"); self._refresh_target_list("상영오픈")
            self._refresh_event_theaters()
            if c.get("days"):
                self.dd_days.set(str(c.get("days")))
            for entry, val in ((self.e_hook, c.get("webhook", "")), (self.e_ment, c.get("mention", "")),
                               (self.e_bot, c.get("bot_token", "")), (self.e_uid, c.get("user_id", "")),
                               (self.e_cid, c.get("cgv_id", "")), (self.e_2cap, c.get("twocaptcha", ""))):
                entry.delete(0, "end"); entry.insert(0, val or "")
            self._save()
            self._put(f"설정 불러옴: {path}")
        except Exception as e:  # noqa: BLE001
            self._put(f"불러오기 실패: {e}")

    def _on_seat_success(self, info):
        """워커 스레드에서 호출 — 중복키 영속화 + 메인스레드 알림 큐로 전달."""
        try:
            self.grabbed_keys.add(info.get("key", ""))
            _save_grabbed(self.grabbed_keys)
        except Exception:  # noqa: BLE001
            pass
        self.success_q.put_nowait(info)

    def _alert(self, info):
        try:
            import winsound
            for _ in range(3):
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:  # noqa: BLE001
            try:
                self.bell()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.deiconify(); self.lift()
            self.attributes("-topmost", True)
            self.after(1500, lambda: self.attributes("-topmost", False))
        except Exception:  # noqa: BLE001
            pass
        reached = info.get("reached", True)
        self.stat.configure(text="● 좌석 선점! 결제하세요" if reached else "● 좌석 선택됨! 결제하기 누르세요",
                            text_color=RED)
        tail = ("해당 크롬 창에서 결제하세요." if reached
                else "해당 크롬 창에서 '결제하기'를 직접 눌러 결제페이지로 넘어간 뒤 결제하세요.")
        msg = (f"좌석을 잡았습니다!\n\n{info.get('movie','')} · {info.get('theater','')}\n"
               f"{info.get('date','')} {info.get('time','')}  좌석 {info.get('seat','')}\n\n{tail}")
        self._put(f"🔔🔔 [{info.get('tag','')}] 좌석 {'선점' if reached else '선택(결제하기 필요)'}! "
                  f"{info.get('seat','')} — 크롬 창에서 결제하세요")
        try:
            from tkinter import messagebox
            messagebox.showinfo("CGV 좌석 선점 완료", msg)
        except Exception:  # noqa: BLE001
            pass

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
        def _num(w):
            v = "".join(ch for ch in w.get() if ch.isdigit())
            return int(v) if v else None
        return {
            "persons": {"일반": int(ns["gen"].get()), "청소년": int(ns["teen"].get()), "우대": int(ns["pref"].get())},
            "preferred": [s.strip() for s in ns["seats"].get().split(",") if s.strip()],
            "only_preferred": bool(ns["only"].get()),
            "prefer": ns["pos"].get(),
            "interval": int(ns["int"].get()),
            "row_from": ns["rf"].get().strip().upper() if "rf" in ns else "",
            "row_to": ns["rt"].get().strip().upper() if "rt" in ns else "",
            "num_from": _num(ns["nf"]) if "nf" in ns else None,
            "num_to": _num(ns["nt"]) if "nt" in ns else None,
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
            "time_from": "" if ns.get("tfrom") is None or ns["tfrom"].get() == "전체" else f"{ns['tfrom'].get()}:00",
            "time_to": "" if ns.get("tto") is None or ns["tto"].get() == "전체" else f"{ns['tto'].get()}:59",
            "screen_type": "" if ns["screen"].get() == "전체" else ns["screen"].get(),
            "webhook": self.e_hook.get().strip(),
            "mention": self.e_ment.get().strip(),
        }
        t.update(self._read_ps(ns))
        return t

    def _save(self):
        save_cfg(self._current_cfg())
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
        if c.get("bot_token"):
            self.e_bot.insert(0, c["bot_token"])
        if c.get("user_id"):
            self.e_uid.insert(0, c["user_id"])
        if c.get("dup_prevent"):
            self.sw_dup.select()
        if c.get("auto_relogin"):
            self.sw_relogin.select()
        if c.get("cgv_id"):
            self.e_cid.insert(0, c["cgv_id"])
        if c.get("cgv_pw"):
            self.e_cpw.insert(0, c["cgv_pw"])
        if c.get("twocaptcha"):
            self.e_2cap.insert(0, c["twocaptcha"])

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
            p = t.get("persons") or {}
            ppl = "".join(f"{k[0]}{v}" for k, v in p.items() if int(v) > 0) or "일2"
            extra = f" · {ppl}"
            if t.get("preferred"):
                extra += " · 좌석 " + ",".join(t["preferred"]) + ("만" if t.get("only_preferred") else "")
            rf, rt = t.get("row_from") or "", t.get("row_to") or ""
            nf, nt = t.get("num_from"), t.get("num_to")
            if rf or rt or nf is not None or nt is not None:
                extra += (f" · 구역 {rf or '?'}~{rt or '?'}열 "
                          f"{nf if nf is not None else ''}~{nt if nt is not None else ''}번")
            tw = t.get("time_from") or ""
            tw2 = t.get("time_to") or ""
            timelbl = t.get("time") or ((f"{tw}~{tw2}") if (tw or tw2) else "전체")
            txt = f"{i+1}. {t['movie']} · {t['theater']} · {t['date']} · {timelbl}{extra}"
            ctk.CTkLabel(row, text=txt, text_color="white", justify="left").pack(side="left", padx=10, pady=6)
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

    # ---------- 로그인 준비 (마스터 1회 로그인 → 세션 저장) ----------
    def _prelogin(self):
        if self._logging_in:
            return
        self._logging_in = True
        self._put("[로그인] 크롬을 띄웁니다. 로그인(캡챠 포함)만 마치면 세션을 저장하고 창을 닫습니다.")

        def worker():
            from cgv_macro.replayer import Grabber
            try:
                g = Grabber(headless=False).__enter__()
                ok = g.ensure_login(timeout_s=600)
                if ok:
                    g.export_state(STATE_JSON)   # 세션(쿠키)을 저장 → 각 창에 주입
                try:
                    g.close()
                except Exception:  # noqa: BLE001
                    pass
                if ok:
                    self.master_ready = True
                    self._put("[로그인] 완료·세션 저장됨. '② 감시 시작'하면 대상마다 창이 뜨고 로그인이 공유됩니다.")
                    self.after(0, lambda: self.stat.configure(text="● 로그인됨(대기)", text_color=GREEN))
                else:
                    self._put("[로그인] 실패/시간초과 — 다시 시도하세요.")
            except Exception as e:  # noqa: BLE001
                self._put(f"[로그인] 오류: {e}")
            finally:
                self._logging_in = False
        threading.Thread(target=worker, daemon=True).start()

    # ---------- 감시 (대상마다 독립 창 · 로그인 세션 공유 · 진짜 병렬) ----------
    def _watch_start(self):
        if self._active > 0:
            return
        if not os.path.exists(RECIPE):
            self._put("먼저 '설정' 탭에서 예매 흐름을 1회 녹화하세요."); return
        if not (self.master_ready or os.path.exists(STATE_JSON)):
            self._put("먼저 '① 로그인 준비'로 1회 로그인하세요(세션이 각 창에 공유됩니다)."); return
        recipe = json.load(open(RECIPE, encoding="utf-8"))
        webhook = self.e_hook.get().strip()
        bot = self.e_bot.get().strip(); uid = self.e_uid.get().strip()
        notifier = None
        if webhook or (bot and uid):
            from cgv_macro.notifier import DiscordNotifier
            notifier = DiscordNotifier(webhook, self.e_ment.get().strip(), bot_token=bot, user_id=uid)
            self._put("알림 방식: " + ("봇 DM" if (bot and uid) else "웹훅(채널)"))
        self._notifier = notifier

        from cgv_macro.watcher import Watcher
        from cgv_macro.event_watch import EventWatcher

        self._close_idle_workers()   # 이전에 남은 유휴 창 정리

        relogin_cfg = {
            "enabled": bool(self.sw_relogin.get()),
            "cgv_id": self.e_cid.get().strip(),
            "cgv_pw": self.e_cpw.get(),
            "twocaptcha": self.e_2cap.get().strip(),
        }
        dup_prevent = bool(self.sw_dup.get())

        def pos(i):
            return (30 + (i % 4) * 300 + (i // 4) * 40, 30 + (i % 3) * 200)

        # 대상마다 '팩토리'(새 워커 생성기)를 만든다 → 멈추면 그 창만 자동 재시작(7번)
        factories = []
        i = 0
        for t in list(self.cancel_list):
            factories.append((lambda t=t, i=i: Watcher(
                recipe, t, notifier, storage_state=STATE_JSON, win_pos=pos(i),
                tag=f"취소#{i+1} {t.get('movie','')[:6]}",
                grabbed_keys=self.grabbed_keys, on_success=self._on_seat_success,
                relogin_cfg=relogin_cfg, dup_prevent=dup_prevent))); i += 1
        for t in list(self.open_list):
            factories.append((lambda t=t, i=i: Watcher(
                recipe, t, notifier, storage_state=STATE_JSON, win_pos=pos(i),
                tag=f"오픈#{i+1} {t.get('movie','')[:6]}",
                grabbed_keys=self.grabbed_keys, on_success=self._on_seat_success,
                relogin_cfg=relogin_cfg, dup_prevent=dup_prevent))); i += 1
        if self.event_theaters:
            ths = [(self.theaters[nm][0], nm) for nm in self.event_theaters if nm in self.theaters]
            if ths:
                base = self._read_ps(self.ns_event)
                factories.append((lambda i=i: EventWatcher(
                    recipe, ths, base, notifier, days=int(self.dd_days.get()),
                    storage_state=STATE_JSON, win_pos=pos(i),
                    tag=f"무대인사#{i+1}", on_success=self._on_seat_success))); i += 1
        if not factories:
            self._put("감시할 대상이 없습니다. 각 탭에서 대상/극장을 추가하세요."); return

        self._save()
        self.watch_stop.clear()
        self.workers = [None] * len(factories)
        self._active = len(factories)
        self._put(f"감시 시작 — 창 {len(factories)}개를 띄워 '동시에' 감시합니다(대상마다 1개, 로그인 공유).")

        for slot, fac in enumerate(factories):
            threading.Thread(target=self._supervise, args=(slot, fac), daemon=True).start()
        self.b_start.configure(state="disabled")
        self.b_stop.configure(state="normal", fg_color=RED, hover_color=RED_DK)
        self.stat.configure(text=f"● 감시 중 (창 {len(factories)})", text_color=GREEN)

    def _supervise(self, slot, factory):
        """한 대상의 창을 관리 — '크래시(예외)'일 때만 자동 재시작(최대 5회).
        정상 종료(중복 건너뜀/좌석 선점/해석 실패 등)는 재시작하지 않는다."""
        restarts = 0
        try:
            while not self.watch_stop.is_set():
                w = factory()
                self.workers[slot] = w
                crashed = False
                try:
                    w.run(self.watch_stop, log=self._put)
                except Exception as e:  # noqa: BLE001
                    self._put(f"[감시] 오류: {e}")
                    crashed = True
                if self.watch_stop.is_set() or getattr(w, "held_payment", False) or not crashed:
                    break   # 중지/선점/정상종료 → 재시작 안 함
                try:
                    w.close()
                except Exception:  # noqa: BLE001
                    pass
                restarts += 1
                if restarts > 5:
                    self._put(f"[슬롯{slot+1}] 반복 오류 5회 — 이 창 감시 중단"); break
                self._put(f"[슬롯{slot+1}] 창이 오류로 멈춰 재시작({restarts}/5)")
                for _ in range(6):   # ~3초 대기(중지 반응)
                    if self.watch_stop.is_set():
                        break
                    time.sleep(0.5)
        finally:
            self.after(0, self._worker_done)

    def _worker_done(self):
        self._active -= 1
        if self._active <= 0:
            self._watch_ended()

    def _close_idle_workers(self):
        keep = []
        for w in self.workers:
            if w is None:
                continue
            if getattr(w, "held_payment", False):
                keep.append(w)   # 결제 대기 창은 유지
            else:
                try:
                    w.close()
                except Exception:  # noqa: BLE001
                    pass
        self.workers = keep

    def _watch_stop(self):
        self.watch_stop.set()
        self.stat.configure(text="● 중지 중…", text_color="#E0A030")

    def _watch_ended(self):
        held = sum(1 for w in self.workers if getattr(w, "held_payment", False))
        self._close_idle_workers()
        self.b_start.configure(state="normal")
        self.b_stop.configure(state="disabled", fg_color="#33333A")
        self.stat.configure(text="● 로그인됨(대기)" if self.master_ready else "● 대기",
                            text_color=GREEN if self.master_ready else SUB)
        extra = f" 선점한 결제창 {held}개는 그 창에서 결제하세요." if held else ""
        self._put(f"감시 중지.{extra}")

    def _on_close(self):
        self.watch_stop.set(); self.rec_stop.set()
        for w in self.workers:
            if w is None:
                continue
            try:
                w.close()
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


def _load_grabbed() -> set:
    try:
        return set(json.load(open(GRABBED_JSON, encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return set()


def _save_grabbed(keys: set) -> None:
    try:
        json.dump(sorted(keys), open(GRABBED_JSON, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    App().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
