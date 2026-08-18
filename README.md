# CGV 상영 오픈 / 취소표 감지 + 디스코드 알림

지정한 영화·극장·날짜·시간대의 **상영(회차) 오픈**, **잔여석 발생**, **매진→잔여석(취소표)** 을 주기적으로 감시하고 디스코드로 알립니다.

로그인·브라우저 필요 없음. CGV 공개 예매 API 를 가볍게 폴링합니다. (실제 예매/결제는 알림을 받고 사용자가 직접 하세요.)

> ⚠️ 개인 용도로 적당한 주기(기본 45초+지터)로 사용하세요. 주기를 과도하게 낮추면 CGV 가 IP 를 막을 수 있습니다.

---

## 동작 방식

CGV 개편 사이트는 `cgv.co.kr/api/v1/booking/*` 로 예매 데이터를 공개 제공합니다. 이 도구는 그 API 를 폴링합니다.

- 회차+잔여석: `searchSchByMov` (파라미터 `movNo`, `siteNo`, `scnYmd`, `rtctlScopCd`)
- 영화코드: `searchAtktTopPostrList`, 극장코드: `searchRegnList`
- 응답의 `frSeatCnt`(잔여석), `cpSeatCnt`(총좌석), `scnsrtTm`(시작시각), `scnsNm`(상영관) 을 읽습니다.

첫 폴링은 현재 회차를 **조용히 기준선으로 저장**하고 요약 1건만 보냅니다. 이후부터 **새 상영 오픈 / 잔여석 발생 / 매진→잔여(취소표)** 변화가 생길 때만 알립니다. (시작하자마자 수십 개 알림이 쏟아지지 않게)

---

## 방법 A) exe 프로그램 (권장, 코딩 불필요)

1. [Python 3.10+](https://www.python.org/downloads/) 설치 (설치 시 "Add Python to PATH" 체크).
2. `build_exe.bat` **더블클릭** → 자동으로 단일 exe 생성. (브라우저/크로미움 없이 작고 빠름)
3. 결과물: `dist\CGV-Ticket-Watcher.exe`
4. 실행 → 대상 추가 → 디스코드 웹훅 입력 → **설정 저장** → **감시 시작**.

> exe 없이 바로 띄우려면(파이썬 설치 상태): `python cgv_gui.py` (또는 `run.bat` 더블클릭)

화면 구성: 감시 대상 표(추가/수정/삭제), 폴링·알림 설정, 디스코드 웹훅, 감시 시작/중지, 실시간 로그.

---

## 방법 B) CLI

```bash
python -m venv .venv && .venv\Scripts\activate   # (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy config.example.yaml config.yaml             # 편집 후
python -m cgv_macro --config config.yaml         # 감시 시작
python -m cgv_macro --once                        # 1회만 테스트
python -m cgv_macro --test-discord                # 웹훅 테스트
```

---

## 설정 (config.yaml)

전체 항목은 `config.example.yaml` 참고. 핵심:

```yaml
targets:
  - name: "오디세이 센텀시티"
    movie: "오디세이"          # 영화명(부분일치) 또는 movie_code(movNo)
    theater: "센텀시티"        # 극장명(부분일치) 또는 theater_code(siteNo)
    date: "2026-08-19"         # YYYY-MM-DD
    time_from: "00:00"         # 비우면 하루 전체
    time_to: "23:59"
    screen_type: ""            # 선택: IMAX/4DX/SCREENX 등 부분일치
poll:
  interval_seconds: 45         # 30 이상 권장
  jitter_seconds: 15
alerts:
  on_showtime_open: true
  on_seats_available: true
  on_soldout_to_available: true   # 취소표
  min_remaining_seats: 1
discord:
  webhook_url: "https://discord.com/api/webhooks/XXXX/YYYY"
  mention: ""                  # 예: "<@사용자ID>"
```

감시 대상은 `targets:` 에 여러 개 추가 가능. GUI 에서는 표의 추가 버튼으로.

디스코드 웹훅 만드는 법: 서버 설정 → 연동 → 웹훅 → 새 웹훅 → "웹후크 URL 복사".

### 설정·상태·로그 저장 위치
실행 위치와 무관한 고정 폴더에 저장되어 **exe 를 다시 빌드/이동해도 유지**됩니다.
- Windows: `%APPDATA%\CGV-Ticket-Watcher\` (config.yaml / state.json / logs)

---

## 디스코드 알림 예시

```
🟢 잔여석 발생 / 🎟️ 취소표 발생 (매진→잔여) / 🎬 상영 오픈
영화: 오디세이   극장: CGV 센텀시티   상영관: IMAX관 (IMAX LASER 2D)
날짜/시간: 2026-08-19 22:10   상태: 잔여 77/422석
예매 페이지: https://cgv.co.kr/cnm/movieBook/movie
```

오류가 연속으로 발생하면 에러 알림도 전송됩니다(도배 방지 쿨다운).

---

## 상시 실행 (PC를 켜두거나 서버 배포)

PC 에서 실행하면 PC 가 켜져 있어야 감시가 동작합니다.

- Windows 자동 실행: 작업 스케줄러 → 로그온 시 → `run.bat` 실행.
- Linux systemd 예시:
```ini
[Unit]
Description=CGV watcher
After=network-online.target
[Service]
WorkingDirectory=/path/to/cgv-macro
ExecStart=/path/to/cgv-macro/.venv/bin/python -m cgv_macro --config /path/to/config.yaml
Restart=on-failure
[Install]
WantedBy=multi-user.target
```
(추가 시스템 라이브러리 불필요 — 순수 파이썬 + 표준 라이브러리.)

---

## CGV 구조가 바뀌면? (수정 위치)

감지가 안 되면 **`cgv_macro/cgv_api.py`** 한 파일만 보면 됩니다. API 주소·파라미터·응답 필드명이 모두 거기 있습니다. 브라우저 F12 → Network 에서 `api/v1/booking/searchSchByMov` 응답의 실제 필드명을 확인해 맞추면 됩니다.

---

## 산출물 체크리스트
- [x] GUI 앱 `cgv_gui.py` + exe 빌드 `build_exe.bat`
- [x] CLI `python -m cgv_macro`
- [x] 설정 예시 `config.example.yaml`, 실행 스크립트 `run.bat`/`run.sh`
- [x] 디스코드 웹훅 알림 + 예시, 회전 로그(logs/)
- [x] 감시 대상 추가/수정 방법, CGV 구조 변경 시 수정 위치(cgv_api.py)

## 주의
- 잔여석/취소표는 CGV 가 즉시 자동해제하므로, 알림을 받으면 **빠르게 직접 예매**하세요.
- 좌석 자동선택/결제는 하지 않습니다(로그인 필요 + 계정 리스크). 감지·알림 전용입니다.
