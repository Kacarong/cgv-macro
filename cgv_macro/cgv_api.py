"""
CGV 예매 조회 (무인증 HTTP API, 2026-08 실측).

CGV 개편 사이트는 cgv.co.kr/api/v1/booking/* 프록시로 예매 데이터를 공개 제공한다.
로그인/브라우저/대기열 없이 GET 한 번으로 회차+잔여석을 읽을 수 있다.

핵심 엔드포인트:
  - 영화목록:  /api/v1/booking/searchAtktTopPostrList   → movNo, movNm
  - 극장목록:  /api/v1/booking/searchRegnList           → siteNo, siteNm
  - 회차+좌석: /api/v1/booking/searchSchByMov
        params: coCd=A420, movNo, scnYmd(YYYYMMDD), siteNo, rtctlScopCd=1
        각 회차: scnsrtTm(시작시각 'HHMM'), frSeatCnt(잔여석), cpSeatCnt(총좌석),
                 scnsNm(상영관), expoProdNm(영화+포맷), cntlYn(판매통제 Y/N)

구조가 바뀌면 이 파일의 상수/필드명만 고치면 된다.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("cgv_macro")

BASE = "https://cgv.co.kr/api/v1/booking"
CO_CD = "A420"
RTCTL_SCOP_CD = "1"  # '발매통제범위코드' 필수값(값 무관, 존재만 하면 됨)
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
_HEADERS = {"User-Agent": _UA, "Referer": "https://cgv.co.kr/cnm/movieBook/movie"}


class CgvApiError(Exception):
    pass


class CgvRateLimited(CgvApiError):
    """429 등 요청 과다 — 호출부에서 백오프."""
    pass


def _get(path: str, **params) -> Any:
    url = f"{BASE}/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (429, 503):
            raise CgvRateLimited(f"요청 과다({e.code})")
        raise CgvApiError(f"HTTP {e.code} {path}")
    if not isinstance(d, dict):
        raise CgvApiError(f"예상치 못한 응답: {path}")
    if str(d.get("statusCode")) not in ("0", "200"):
        raise CgvApiError(f"{path}: {d.get('statusMessage')}")
    return d.get("data")


@dataclass
class Showtime:
    time: str                 # "09:30"
    screen: str = ""          # 상영관명(scnsNm)
    fmt: str = ""             # 포맷(movkndDsplNm, 예 '4DX 2D')
    remaining: int = -1       # 잔여석(frSeatCnt)
    total: int = -1           # 총좌석(cpSeatCnt)
    soldout: bool = False
    controlled: bool = False  # 판매통제(cntlYn=Y)
    event: str = ""           # 이벤트/특전(무대인사·GV·씨네드쉐프행사 등)
    schedule_id: str = ""     # scnSseq
    scns_no: str = ""         # scnsNo
    raw: dict[str, Any] = field(default_factory=dict)

    def showtime_key(self) -> str:
        return f"{self.time}|{self.scns_no}|{self.schedule_id}"


# 무대인사류 이벤트 키워드(회차 raw의 어떤 문자열 필드에든 이 말이 있으면 이벤트로 간주).
# CGV가 필드명을 바꿔도 견디도록 값 자체를 스캔한다.
# 주의: 'GV'는 극장명 'CGV 센텀시티'에 들어가므로 단어경계 정규식으로만 매칭(아래 _GV_RE).
STAGE_EVENT_KEYWORDS = (
    "무대인사", "무대 인사", "관객과의 대화", "관객과의대화",
    "내한", "라이브톡", "라이브 톡", "화상 인사", "화상인사",
    "GV상영", "GV 상영", "싸인회", "사인회",
)
# 'GV'가 독립 토큰일 때만(예: '관객과의 대화(GV)'). 'CGV'는 C-G 사이에 경계가 없어 매칭 안 됨.
_GV_RE = re.compile(r"\bGV\b")
# 극장명(CGV ...)처럼 이벤트가 아닌데 걸리기 쉬운 값은 제외.
_NON_EVENT_RE = re.compile(r"CGV|씨네드쉐프|씨넬리브러리")


def stage_event_label(raw: dict[str, Any], keywords: tuple[str, ...] = STAGE_EVENT_KEYWORDS) -> str:
    """회차 raw dict의 문자열 값들을 훑어 무대인사류 키워드가 있으면 그 값을 반환. 없으면 ''."""
    for v in raw.values():
        if not isinstance(v, str) or not v:
            continue
        if _NON_EVENT_RE.search(v):
            continue  # 극장명 등 오탐 소스 제외
        if _GV_RE.search(v):
            return v.strip()
        for kw in keywords:
            if kw in v:
                return v.strip()
    return ""


def _norm_time(hhmm: str) -> str:
    s = "".join(ch for ch in str(hhmm) if ch.isdigit())
    if len(s) >= 4:
        return f"{s[0:2]}:{s[2:4]}"
    return str(hhmm)


def _to_int(v: Any) -> int:
    try:
        return int(str(v).strip())
    except Exception:  # noqa: BLE001
        return -1


# ---------------- 목록(드롭다운용) ----------------
def list_movies() -> list[tuple[str, str]]:
    """예매 가능한 영화 목록 → [(movNo, movNm), ...]."""
    data = _get("searchAtktTopPostrList", coCd=CO_CD, movNm="", div="", attrCd="") or []
    out = []
    for m in data:
        if m.get("movNo"):
            out.append((str(m.get("movNo")), str(m.get("movNm"))))
    return out


def list_theaters() -> list[tuple[str, str, str]]:
    """극장 목록 → [(siteNo, siteNm, regionNm), ...]."""
    data = _get("searchRegnList", coCd=CO_CD) or []
    out = []
    for reg in data:
        rn = str(reg.get("regnGrpNm", ""))
        for s in reg.get("siteList", []):
            out.append((str(s.get("siteNo")), str(s.get("siteNm")), rn))
    return out


# ---------------- 코드 해석 ----------------
def resolve_movie(movie: str, movie_code: str = "") -> tuple[str, str]:
    """영화명 또는 movNo → (movNo, movNm). 실패 시 CgvApiError."""
    if movie_code:
        return movie_code, movie or movie_code
    data = _get("searchAtktTopPostrList", coCd=CO_CD, movNm="", div="", attrCd="") or []
    cands = [m for m in data if movie and movie in str(m.get("movNm", ""))]
    if not cands:
        # 공백 제거 후 재시도
        key = movie.replace(" ", "")
        cands = [m for m in data if key and key in str(m.get("movNm", "")).replace(" ", "")]
    if not cands:
        names = ", ".join(str(m.get("movNm")) for m in data[:20])
        raise CgvApiError(f"영화 '{movie}' 를 예매목록에서 못 찾음. 현재 목록 예: {names}")
    # 정확 일치 우선
    exact = [m for m in cands if str(m.get("movNm")) == movie]
    m = (exact or cands)[0]
    if len(cands) > 1:
        logger.info("영화 후보 %d개, '%s' 선택", len(cands), m.get("movNm"))
    return str(m.get("movNo")), str(m.get("movNm"))


def resolve_theater(theater: str, theater_code: str = "") -> tuple[str, str, str]:
    """극장명 또는 siteNo → (siteNo, siteNm, regionNm). 실패 시 CgvApiError."""
    if theater_code:
        return theater_code, theater or theater_code, ""
    data = _get("searchRegnList", coCd=CO_CD) or []
    pairs = []  # (site, regionNm)
    for reg in data:
        rn = str(reg.get("regnGrpNm", ""))
        for s in reg.get("siteList", []):
            pairs.append((s, rn))
    cands = [(s, rn) for (s, rn) in pairs if theater and theater in str(s.get("siteNm", ""))]
    if not cands:
        raise CgvApiError(f"극장 '{theater}' 를 못 찾음.")
    exact = [(s, rn) for (s, rn) in cands if str(s.get("siteNm")) == theater]
    s, rn = (exact or cands)[0]
    if len(cands) > 1:
        logger.info("극장 후보 %d개, '%s'(%s) 선택", len(cands), s.get("siteNm"), rn)
    return str(s.get("siteNo")), str(s.get("siteNm")), rn


# ---------------- 회차 조회 ----------------
def fetch_showtimes(mov_no: str, site_no: str, date_yyyymmdd: str) -> list[Showtime]:
    """해당 영화/극장/날짜의 회차 목록. 미오픈이면 빈 리스트."""
    data = _get("searchSchByMov", coCd=CO_CD, movNo=mov_no, scnYmd=date_yyyymmdd,
                siteNo=site_no, rtctlScopCd=RTCTL_SCOP_CD) or []
    out: list[Showtime] = []
    for it in data:
        rem = _to_int(it.get("frSeatCnt"))
        event = ""
        for k in ("videoAddexpCdNm", "prmddNm", "movEtcAttrCd"):
            v = it.get(k)
            if v not in (None, "", "N"):
                event = str(v); break
        st = Showtime(
            time=_norm_time(it.get("scnsrtTm")),
            screen=str(it.get("scnsNm", "")),
            fmt=str(it.get("movkndDsplNm", "")),
            remaining=rem,
            total=_to_int(it.get("cpSeatCnt")),
            soldout=(rem == 0),
            controlled=str(it.get("cntlYn", "N")).upper() == "Y",
            event=event,
            schedule_id=str(it.get("scnSseq", "")),
            scns_no=str(it.get("scnsNo", "")),
            raw=it,
        )
        out.append(st)
    out.sort(key=lambda s: s.time)
    return out
