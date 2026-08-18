# CGV 상영 오픈 / 취소표 감지 + 디스코드 알림

지정한 영화·극장·날짜·시간대의 **상영(회차) 오픈**, **잔여석 발생**, **매진→잔여석(취소표)** 을 주기적으로 감시하고, 조건이 맞으면 **좌석을 자동으로 선택(홀드)한 뒤 디스코드로 즉시 알림**을 보냅니다.

> ⚠️ **결제는 하지 않습니다.** 이 도구는 좌석 선택(홀드)까지만 하고 알림을 보냅니다. 실제 결제는 사용자가 직접 진행하세요.
> ⚠️ CGV 자동화는 CGV 이용약관/계정 정책에 영향을 줄 수 있습니다. 폴링 주기는 30초 이상(+지터)로 제한되어 있으며, 개인 용도로 신중히 사용하세요.

---

## 동작 방식(중요)

CGV 는 개편으로 `https://cgv.co.kr`(Next.js SPA) + `https://api.cgv.co.kr`(JSON API) + Cloudflare 구조입니다. 이 도구는 **Playwright 로 실제 크롬 브라우저를 띄워** 예매 페이지를 열고,

1. 페이지가 스스로 `api.cgv.co.kr` 로 보내는 **스케줄/좌석 JSON 응답을 가로채(capture)** 잔여석을 읽습니다. (DOM 만 긁는 것보다 구조 변화에 강함)
2. 좌석 자동 선택이 필요할 때만 좌석 배치도에서 **DOM 클릭**으로 좌석을 잡습니다.
3. 로그인은 캡차/추가인증 때문에 **최초 1회 수동 로그인** 후 세션(브라우저 프로필)을 저장해 재사용합니다.

---

## 방법 A) GUI / exe 프로그램 (권장, 코딩 불필요)

화면으로 설정·로그인·감시를 다 할 수 있는 GUI 앱(`cgv_gui.py`)과, 그것을 **Windows용 .exe 로 만드는 빌드 스크립트**를 제공합니다.

> ℹ️ .exe 는 **Windows 에서 한 번 빌드**해야 합니다(파이썬 특성상 Windows 실행파일은 Windows 에서만 만들어집니다). 아래 스크립트가 그 과정을 자동으로 해줍니다.

빌드(최초 1회, Windows):
1. [Python 3.10+](https://www.python.org/downloads/) 설치(설치 시 "Add Python to PATH" 체크).
2. 이 폴더의 **`build_exe.bat` 더블클릭** → 의존성 설치 + Chromium 다운로드 + exe 빌드까지 자동.
3. 결과물: `dist\CGV-Ticket-Watcher\CGV-Ticket-Watcher.exe`  (이 폴더 전체를 복사해 사용)

사용:
1. `CGV-Ticket-Watcher.exe` 실행.
2. "감시 대상" 에서 **추가** → 영화/극장/날짜/시간대 입력.
3. 폴링 주기·알림 옵션·좌석 자동선택·**디스코드 웹훅 URL** 입력 후 **설정 저장**.
4. **CGV 로그인** 클릭 → 열린 브라우저에서 로그인 → 창의 "로그인 완료" 클릭.
5. **감시 시작**. 하단 로그창에서 상태를 실시간으로 볼 수 있고, **중지** 로 멈춥니다.

> exe 를 만들지 않고 GUI 만 바로 띄우려면(파이썬 설치된 상태): `python cgv_gui.py`

---

## 방법 B) CLI (설치 후 명령어로 실행)

사전 준비: [Python 3.10+](https://www.python.org/downloads/) 설치 (설치 시 "Add Python to PATH" 체크).

```bat
:: 1) 프로젝트 폴더에서
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium

:: 2) 설정 파일 만들기
copy config.example.yaml config.yaml
:: config.yaml 을 편집(메모장 등)해서 영화/극장/날짜/시간대/디스코드 웹훅 입력

:: 3) 최초 1회 로그인 (브라우저 창이 열림 → CGV 로그인 → 터미널에서 Enter)
python login_setup.py

:: 4) 감시 시작
python -m cgv_macro --config config.yaml
```

`run.bat` 을 더블클릭하면 위 과정(가상환경 생성/설치/실행)을 자동으로 처리합니다.

macOS/Linux 는 동일 과정을 `./run.sh` 로 실행합니다.

---

## 설정 파일 (`config.yaml`)

전체 항목과 설명은 `config.example.yaml` 에 주석으로 있습니다. 핵심만 요약:

```yaml
targets:
  - name: "F1 용산 아이맥스"
    movie: "F1 더 무비"          # 영화명(부분일치)
    theater: "용산아이파크몰"     # 지점명(부분일치)
    date: "2026-08-23"           # YYYY-MM-DD
    time_from: "18:00"           # 감시 시간대 시작
    time_to: "23:59"             # 끝
    screen_type: "IMAX"          # (선택) IMAX/4DX/SCREENX 필터
    booking_url: ""              # (선택) 예매 페이지 URL 을 알면 넣으면 탐색 생략(가장 확실)

poll:
  interval_seconds: 45           # 30 이상 권장
  jitter_seconds: 15             # 0~15초 랜덤 지터

alerts:
  on_showtime_open: true         # 회차 새로 열림
  on_seats_available: true       # 잔여석 발생
  on_soldout_to_available: true  # 매진→잔여(취소표)
  min_remaining_seats: 1

auto_select:
  enabled: true                  # 좌석 자동 선택
  count: 2
  prefer: "center"               # center|front|back|any
  preferred_seats: []            # 예: ["H10","H11"] (있으면 최우선)

discord:
  webhook_url: "https://discord.com/api/webhooks/XXXX/YYYY"
  mention: ""                    # 예: "<@사용자ID>" 또는 "@everyone"
```

### 감시 대상 추가/수정 방법

`config.yaml` 의 `targets:` 리스트에 항목을 추가하면 됩니다. 여러 영화/극장/날짜를 동시에 감시할 수 있습니다. 수정 후 프로그램을 재시작하면 반영됩니다.

```yaml
targets:
  - name: "대상1"
    movie: "..."
    theater: "..."
    date: "2026-08-23"
    time_from: "18:00"
    time_to: "23:59"
  - name: "대상2"          # 두 번째 대상
    movie: "..."
    theater: "..."
    date: "2026-08-24"
    time_from: "10:00"
    time_to: "14:00"
```

---

## 실행 옵션 (CLI)

```bash
python -m cgv_macro --config config.yaml      # 감시 시작(상시)
python -m cgv_macro --once                    # 1회만 폴링(테스트)
python -m cgv_macro --check-login             # 로그인 세션 살아있는지 확인
python -m cgv_macro --test-discord            # 디스코드 웹훅 테스트 전송
python login_setup.py                         # 최초/재로그인 (브라우저 수동 로그인)
```

---

## 디스코드 알림 예시

조건 충족 시 아래와 같은 임베드가 전송됩니다.

```
🟢 잔여석 발생
F1 용산 아이맥스
영화: F1 더 무비      극장: 용산아이파크몰      상영관: IMAX관
날짜/시간: 2026-08-23 21:40      상태: 잔여 12/150석
예매 페이지: https://cgv.co.kr/booking/...
```

좌석 자동 선택이 성공하면 이어서:

```
🪑 좌석 자동 선택 완료 — 결제만 하면 됩니다
좌석: H10, H11
예매 페이지: https://cgv.co.kr/booking/...
```

에러가 연속으로 발생하면(`errors.alert_after_consecutive_failures`) 에러 알림도 전송됩니다(도배 방지 쿨다운 적용).

---

## 로그

- 위치: `logs/cgv_macro.log` (5MB 회전, 5개 보관)
- 레벨: `config.yaml` 의 `logging.level` (DEBUG/INFO/WARNING/ERROR)
- 콘솔에도 동시에 출력됩니다.

---

## 상시 실행 (PC를 켜두거나 서버 배포)

이 도구는 **PC 에서 실행할 경우 PC 가 켜져 있어야** 감시가 동작합니다. 24시간 감시하려면 서버에 두는 것이 좋습니다.

### Windows: 시작 시 자동 실행
작업 스케줄러(Task Scheduler) → 기본 작업 만들기 → 트리거 "로그온할 때" → 동작 "프로그램 시작" → `run.bat` 지정.

### Linux: systemd (예시)
`/etc/systemd/system/cgv-macro.service`:
```ini
[Unit]
Description=CGV showtime/seat watcher
After=network-online.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/cgv-macro
ExecStart=/path/to/cgv-macro/.venv/bin/python -m cgv_macro --config config.yaml
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cgv-macro
journalctl -u cgv-macro -f
```
> 참고: headless 브라우저를 서버에서 돌리려면 `python -m playwright install-deps chromium` 로 시스템 라이브러리를 설치하세요. 서버에는 로그인 세션 폴더(`session/`)를 함께 옮겨야 합니다.

### Docker (예시)
`Dockerfile`:
```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "-m", "cgv_macro", "--config", "config.yaml"]
```
```bash
docker build -t cgv-macro .
docker run -d --name cgv-macro \
  -v "$PWD/config.yaml:/app/config.yaml" \
  -v "$PWD/session:/app/session" \
  -v "$PWD/logs:/app/logs" \
  cgv-macro
```
> 로그인 세션(`session/`)은 컨테이너 밖에서 `login_setup.py` 로 만든 뒤 볼륨으로 마운트하세요(컨테이너 안에서는 GUI 로그인이 어렵습니다).

---

## CGV 페이지 구조가 바뀌면? (수정 위치)

CGV 는 종종 개편됩니다. 감지가 안 되면 **`cgv_macro/selectors.py` 한 파일만** 확인/수정하면 됩니다. 이 파일에 URL·API 호스트·응답 필드 후보·좌석 셀렉터가 전부 모여 있습니다.

확인 절차:
1. 크롬에서 CGV 예매를 **직접 한 번** 진행합니다.
2. `F12 → Network` 탭에서 `api.cgv.co.kr` 요청 중,
   - **회차별 잔여석 수**가 담긴 응답(스케줄) → 그 JSON 의 실제 key 이름을 확인
   - **좌석 배치도**가 담긴 응답 → 좌석 점유/잔여 관련 key 확인
3. `selectors.py` 의 아래 목록에 실제 key 를 추가/수정:
   - `SHOWTIME_TIME_FIELDS` (상영 시각), `REMAINING_SEAT_FIELDS` (잔여석 수), `TOTAL_SEAT_FIELDS`, `SOLDOUT_FIELDS`, `SCREEN_NAME_FIELDS`, `SCHEDULE_ID_FIELDS`
4. 좌석 **클릭**이 안 되면 `F12 → Elements` 로 좌석 셀의 CSS 를 확인해 `SEAT_AVAILABLE_SELECTOR`, `SEAT_LABEL_ATTRS`, `SEAT_CONFIRM_SELECTOR` 를 수정.
5. 로그인 판정이 이상하면 `LOGGED_IN_HINT_SELECTOR`, 예매 진입 경로는 `BOOKING_ENTRY_URL` / 각 target 의 `booking_url` 을 조정.

> 💡 **가장 확실한 방법**: 감시하려는 영화·극장·날짜의 예매 페이지를 브라우저에서 연 뒤 그 URL 을 복사해 `config.yaml` 의 해당 target `booking_url` 에 넣으세요. 그러면 UI 탐색 단계를 건너뛰고 그 페이지의 스케줄/좌석 JSON 을 바로 캡처합니다.

### 현재 채워둔 값(2026-08 기준, 개편 시 달라질 수 있음)
- 사이트: `https://cgv.co.kr` (www→apex 리다이렉트, Cloudflare)
- API 게이트웨이: `https://api.cgv.co.kr` (POST-RPC 스타일 엔드포인트)
- 로그인(OIDC): `https://oidc.cgv.co.kr`
- 응답 필드/좌석 셀렉터는 **후보 목록**으로 넣어 두었습니다. 실제 예매를 한 번 캡처해 정확한 key/CSS 로 좁히면 안정성이 크게 올라갑니다.

---

## 산출물 체크리스트
- [x] README (이 문서)
- [x] GUI 앱 `cgv_gui.py` + exe 빌드 `build_exe.bat` / `cgv_gui.spec`
- [x] 설정 파일 예시 `config.example.yaml`
- [x] 실행 스크립트 `run.bat` / `run.sh`
- [x] 최초 로그인 스크립트 `login_setup.py`
- [x] 디스코드 웹훅 알림 (`cgv_macro/notifier.py`) + 예시
- [x] 로그 출력 (`logs/cgv_macro.log`)
- [x] 감시 대상 추가/수정 방법 (위 참고)
- [x] CGV 구조 변경 시 수정 위치 (`cgv_macro/selectors.py`)

---

## 알려진 한계 / 주의
- CGV 예매 UI 자동 탐색(`booking_url` 미지정 시)과 좌석 클릭은 **사이트 구조에 의존**합니다. 개편 직후엔 `selectors.py` 조정이 필요할 수 있습니다.
- 자동 로그인은 지원하지 않습니다(캡차/보안). 세션 만료 시 `python login_setup.py` 로 다시 로그인하세요.
- 좌석 홀드는 CGV 가 일정 시간 후 자동 해제합니다. 알림을 받으면 **빠르게 결제**를 마치세요.
- 과도한 폴링은 IP 차단/계정 제재 위험이 있습니다. 기본값(45초+지터)을 크게 낮추지 마세요.
