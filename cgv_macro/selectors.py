"""
================================================================
CGV 사이트 구조 의존부 — "페이지 구조가 바뀌면 여기만 고치세요"
================================================================
CGV 는 개편으로 https://cgv.co.kr (Next.js SPA) + https://api.cgv.co.kr (JSON API)
구조를 씁니다. 이 파일은 사이트 구조에 의존하는 모든 값(URL, API 호스트, 응답 필드
후보, DOM 셀렉터)을 한 곳에 모아둔 곳입니다. CGV 가 바뀌어 감지가 안 되면 아래 값을
브라우저 개발자도구(F12 → Network / Elements)로 확인해 수정하면 됩니다.

확인 방법(요약, 자세히는 README):
  1) 크롬에서 CGV 예매를 직접 한 번 진행한다.
  2) F12 → Network 탭에서 api.cgv.co.kr 로 가는 요청 중,
     - 상영시간표(회차별 잔여석 수)를 담은 응답
     - 좌석 배치도(좌석별 점유/여부)를 담은 응답
     을 찾아 Response 를 본다.
  3) 그 JSON 의 실제 key 이름을 아래 *_FIELDS 목록에 추가/수정한다.
  4) 좌석 클릭이 안 되면 Elements 탭에서 좌석 셀의 CSS 를 확인해 SEAT_* 셀렉터를 고친다.
"""
from __future__ import annotations

# ---------- 기본 URL ----------
SITE_BASE = "https://cgv.co.kr"
WWW_BASE = "https://www.cgv.co.kr"
API_HOST = "api.cgv.co.kr"            # 스케줄/좌석 JSON 이 오가는 게이트웨이 호스트
OIDC_HOST = "oidc.cgv.co.kr"          # 로그인(OIDC) 호스트
LOGIN_URL = "https://cgv.co.kr/mypage/login"   # 로그인 진입(개편으로 바뀔 수 있음)

# 예매 시작 페이지(영화/극장/날짜 선택 UI). 개편 시 실제 경로로 교체.
BOOKING_ENTRY_URL = "https://cgv.co.kr/booking"

# ---------- 응답(JSON) 필드 후보 ----------
# 스케줄 응답의 "회차 배열"이 들어있을 만한 key 후보(응답 최상위 또는 중첩 탐색).
SCHEDULE_LIST_HINT_KEYS = [
    "scheduleList", "schdlList", "playSchdlList", "list", "items", "data",
    "movieFormList", "timeList",
]
# 한 회차 항목에서 "상영 시작시각"을 담는 key 후보(예: "1920", "19:20", "192000").
SHOWTIME_TIME_FIELDS = [
    "playStartTime", "startTime", "schdlSrtTime", "playStartTm", "time",
    "screeningTime", "srtTime",
]
# "잔여석 수" key 후보.
REMAINING_SEAT_FIELDS = [
    "restSeatCnt", "rmndSeatCnt", "remainSeatCnt", "leftSeatCnt", "restSeat",
    "restSeatCount", "seatRemainCnt", "atktRvseatCnt",
]
# "전체 좌석 수" key 후보.
TOTAL_SEAT_FIELDS = [
    "totSeatCnt", "totalSeatCnt", "allSeatCnt", "seatCnt", "totSeat",
]
# "매진 여부" 플래그 key 후보(값이 'Y'/'N' 또는 true/false).
SOLDOUT_FIELDS = ["soldOutYn", "seatSoldOutYn", "isSoldOut", "soldout"]
# 상영관 이름 key 후보.
SCREEN_NAME_FIELDS = ["screenNm", "scrnNm", "screenName", "theaterScreenNm", "hallName"]
# 회차 식별자(좌석 페이지로 이동할 때 사용) key 후보.
SCHEDULE_ID_FIELDS = ["schdlNo", "scheduleNo", "playSchdlNo", "schdlId", "screeningId"]

# ---------- DOM 셀렉터(좌석 자동선택용) ----------
# 좌석 배치도의 "선택 가능한 좌석" 요소. 여러 후보를 콤마로 OR 매칭.
SEAT_AVAILABLE_SELECTOR = (
    "[class*='seat'][class*='able']:not([class*='disable']):not([class*='sold']),"
    "button.seat:not([disabled]):not([class*='sold']),"
    "[data-seat-status='able']"
)
# 좌석 요소에서 좌석 이름(예: H10)을 담는 속성/텍스트.
SEAT_LABEL_ATTRS = ["data-seat-nm", "data-seatnm", "aria-label", "title"]
# 좌석 선택 후 "다음/결제" 버튼(누르면 좌석이 홀드됨). 결제 자체는 진행하지 않음.
SEAT_CONFIRM_SELECTOR = (
    "button:has-text('다음'),button:has-text('선택완료'),button:has-text('좌석선택완료')"
)
# 로그인 완료 판정에 쓰는 요소(로그인 상태면 보이는 것). README 참고해 교체 가능.
LOGGED_IN_HINT_SELECTOR = "a[href*='logout'], button:has-text('로그아웃'), [class*='myPage']"
