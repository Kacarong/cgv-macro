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


def _get(path: str, **params) -> Any:
    url = f"{BASE}/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.load(r)
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
    schedule_id: str = ""     # scnSseq
    scns_no: str = ""         # scnsNo
    raw: dict[str, Any] = field(default_factory=dict)

    def showtime_key(self) -> str:
        return f"{self.time}|{self.scns_no}|{self.schedule_id}"


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
        st = Showtime(
            time=_norm_time(it.get("scnsrtTm")),
            screen=str(it.get("scnsNm", "")),
            fmt=str(it.get("movkndDsplNm", "")),
            remaining=rem,
            total=_to_int(it.get("cpSeatCnt")),
            soldout=(rem == 0),
            controlled=str(it.get("cntlYn", "N")).upper() == "Y",
            schedule_id=str(it.get("scnSseq", "")),
            scns_no=str(it.get("scnsNo", "")),
            raw=it,
        )
        out.append(st)
    out.sort(key=lambda s: s.time)
    return out
