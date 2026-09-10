# watchog

> 어투·산출물 격식·공개 저장소 위생·세션 기록 규칙은 전역 설정에 있으므로 여기 다시 적지 않는다.
> 여기엔 **이 프로젝트에서만 참인 것**만 둔다. 절대 경로·비밀 값·개인정보는 적지 않는다 (그건 dev-notes `참조/watchog.md`).

## 한 줄 소개

"○○ 알람 받고 싶어"를 자연어로 받아 changedetection.io 에 감시를 등록하고 ntfy 로 폰에 푸시하는 개인 알림 허브.
설계 정본은 [docs/plan-v1.md](docs/plan-v1.md). 감시 엔진은 만들지 않고 그 위의 MCP 서버·검증·알림층만 만든다.

## 실행 · 검증

```
pip install -r requirements.txt
cp .env.example .env                 # 토픽 생성 방법은 .env.example 주석
docker compose up -d                 # changedetection.io + 브라우저 컨테이너, UI http://localhost:5000
python -m watchog test               # ntfy 경로 확인 (폰에 알림이 오면 성공)
python -m watchog run                # 자체 워처 1회 실행 (현재 워처 없음)
```

- API 키는 첫 기동 때 생성된다 → UI Settings → API → `.env` 의 `CDIO_API_KEY`.
- 테스트·린트는 아직 없다. 붙이면 여기 갱신.
- "계약" 표의 항목을 바꿀 땐 사용하는 곳을 먼저 grep 하고 영향 표를 보여준 뒤 고친다.

## 구조 (코드 밖에서 알아야 하는 것만)

| 경로 | 역할 | 비고 |
|---|---|---|
| `docker-compose.yml` | changedetection.io + sockpuppetbrowser | 포트는 127.0.0.1 만. 데이터는 `data/changedetection/` (gitignore, API 키 포함) |
| `watchog/cdio.py` | changedetection.io REST 클라이언트 | API 모양을 아는 유일한 곳. MCP 서버·CLI 가 이걸 통해 간다 |
| `watchog/core/` | Alert 모델·dispatch·seen-set·설정 로딩 | v0.5 자체 워처용. LLM 판정층(2·3층)에서 재사용 |
| `watchog/sinks/ntfy.py` | ntfy 발송 | JSON 포맷 사용 — 헤더 방식은 한글이 깨진다 |
| `config/watch.yaml` | 자체 워처 설정 | `${VAR}` 참조만 있어 커밋됨 |
| `scripts/register_*.py` | 1주차 수동 등록 (채용·항공권) | 재실행 안전. MCP 서버가 생기면 이 스크립트가 도구로 바뀐다 |
| `docs/adr/` | 설계 결정 | 결정마다 파일 하나 |

## 계약 — 다른 곳이 의존하는 것

| 계약 | 정의 위치 | 사용하는 곳 | 담당 | 바꿀 때 |
|---|---|---|---|---|
| `.env` 키 `NTFY_TOPIC`, `NTFY_TOPIC_{MOVIE,JOBS,FLIGHT}`, `CDIO_URL`, `CDIO_API_KEY` | `.env.example` | `sinks/ntfy.py`, `cdio.py`, `config/watch.yaml`(`${NTFY_TOPIC}`), `.github/workflows/poll.yml`(secrets) | 나 | 넷 다 같이. Actions secrets 도 |
| ntfy 토픽 규칙 `watchog-<모듈>-<랜덤>` | plan-v1 §6 | changedetection.io 의 알림 URL(`ntfys://ntfy.sh/<토픽>`), 폰 구독 | 나 | 폰 구독을 다시 해야 한다 |
| changedetection.io 감시 `tag` = 모듈 이름 (`movie`/`jobs`/`flight`/`gamenews`) | plan-v1 §3 기본 모듈 | `cdio.add_watch(tag=)`, 앞으로 MCP `list_watches` 필터 | 나 | 기존 감시의 tag 도 갱신 |
| `Alert` 데이터클래스 필드 | `core/notify.py` | `sinks/*.py`, `cli.py` | 나 | sink 전부 |
| `data/seen.json` 형식 `{"seen": {"ns:id": iso}}` | `core/state.py` | Actions 가 커밋해 되돌림 | 나 | 기존 파일 마이그레이션 |

## 규칙

- **판정은 코드, LLM 은 접수와 서술만.** LLM 이 만든 설정은 검증(`test_parse`) 통과 후에만 등록한다.
- fail-loud: 에러도 ntfy `sys` 토픽으로. 조용히 죽지 않는다.
- 폴링은 보수적으로. Google Flights 특가 12시간, 커뮤니티 피드·채용 1시간. `MINIMUM_SECONDS_RECHECK_TIME=300` 이 하한.
- CGV 는 robots.txt 로 지명 검색엔진 외 접근을 거부한다 ([docs/cgv.md](docs/cgv.md) §4). 개인용 저빈도라도 간격을 넓게.
- ntfy 토픽·API 키는 어디에도 적지 않는다. `.env` 와 `data/changedetection/` 만.

## 설계 결정 (왜 이렇게 했나)

| 날짜 | 결정 | 이유 | ADR |
|---|---|---|---|
| 2026-08-26 | 자체 크롤러 대신 changedetection.io 엔진 재사용 | 파서 유지보수·봇 차단을 검증된 엔진에 위임, 우리는 얇고 새로운 층만 | [plan-v1 §2](docs/plan-v1.md) |
| 2026-08-26 | 알림은 ntfy | 카톡 나에게 보내기는 푸시가 안 뜨고, 알림톡은 사업자 전용 | [README](README.md) |
| 2026-09-11 | 감시 엔진은 로컬 PC Docker | 결정을 미루는 비용이 더 컸다. 브라우저 컨테이너가 저가 VPS 엔 무겁다 | [0001](docs/adr/0001-changedetection-로컬-docker.md) |
| 2026-09-11 | 항공권을 기본 모듈에 추가, 캘린더 동기화는 보류 | 사용자 결정 | [plan-v1 §3·§6-1](docs/plan-v1.md) |

## 하지 말 것

| 시도 | 결과 | 대신 |
|---|---|---|
| ntfy 헤더 방식으로 한글 제목 | latin-1 강제라 깨진다 | JSON 바디 (`sinks/ntfy.py`) |
| CGV 구 `iframeTheater.aspx` 파싱 | SPA 셸 HTML 200 이 와서 조용히 실패 | [docs/cgv.md](docs/cgv.md) 의 신규 API·레거시 백엔드 |
| GitHub Actions 에서 changedetection.io | 상시 프로세스를 못 둠 | 로컬 Docker ([0001](docs/adr/0001-changedetection-로컬-docker.md)) |
| `load_dotenv()` 를 인자 없이 stdin 스크립트에서 | 프레임 탐색이 실패 (Python 3.14) | `load_dotenv(find_dotenv(usecwd=True))` |

## 관련 문서

- 세션 기록·다음 할 일: dev-notes 레포 `다음세션_시작메모.md` 의 `watchog` 섹션
- 환경·경로·비밀값 위치: dev-notes 레포 `참조/watchog.md`
