# watchog

놓치면 안 되는 것들을 대신 지켜보다가, 조건에 맞는 순간이 오면 폰으로 찔러주는
개인용 알림 허브.

이름은 포켓몬 **보르그(Watchog)** 에서 왔다. 소프트웨어에서 감시 프로세스를 뜻하는
`watchdog` 과 한 글자 차이라, 둘 중 뭘 알든 무슨 물건인지 읽힌다.

> **설계 정본은 [`docs/plan-v1.md`](docs/plan-v1.md).** 감시 엔진은 [changedetection.io](https://github.com/dgtlmoon/changedetection.io) 를
> 재사용하고, 이 레포는 그 위의 MCP 서버(자연어 접수·검증·승격)와 ntfy 알림층을 만든다.
> 전신: [`docs/design-v0.md`](docs/design-v0.md) (MyPlayScheduler, 게임 뉴스 특화).
> **상태 (2026-09-12)**: 레시피 2개(채용·항공권 핫딜) 7소스 가동, MCP 서버 4도구, Actions 러너(dry-run), 일일 점검. 폰 수신 확인·영화관은 사용자 몫.

## 왜 만드는가

영화 예매 오픈, 채용 공고, 항공권 핫딜, 게임 패치 일정 — 겉보기엔 다른 일이지만 구조가 같다.

```
[주기적 수집] → [조건 매칭 / 변화 감지] → [중복 제거] → [푸시]
```

다른 건 수집기와 매칭 규칙뿐이라, 프로젝트를 넷 만들 이유가 없다. 그리고 수집기와 변화 감지는
이미 changedetection.io 가 잘 한다. 직접 만들 것은 **"○○ 알람 받고 싶어"를 등록으로 바꾸는 층**이다.

## 구조

```
사용자 (자연어)
   │
Claude ── MCP 서버 ── recipes/*.yaml (정본) ──┬── changedetection.io (로컬 Docker, 브라우저 소스) ──┐
   add / list / remove / test_parse          └── GitHub Actions `poll` (피드·requests 소스)     ├── ntfy.sh ── 폰
                                                                                                │      └ 유용/무시 버튼 → feedback 토픽 → history.sqlite
   ops daily (하트비트 · 레시피 재검사 · 엔진 상태) ── sys 토픽 ──────────────────────────────────┘
```

```
recipes/             주제 하나 = 파일 하나. 이게 정본이다
watchog/
├─ recipes.py        레시피 로더·검증·changedetection.io 동기화
├─ extract.py        robots 확인 · 피드 자동 발견 · 셀렉터 후보 · test_parse
├─ mcp_server.py     Claude 가 부르는 4개 도구
├─ runner.py         changedetection.io 없이 레시피를 도는 러너 (Actions 용)
├─ history.py        알림 이력 sqlite + 유용/무시 피드백
├─ ops.py            하루 한 번: 하트비트 · 재검사 · 엔진 상태
├─ cdio.py           changedetection.io REST 클라이언트 — API 모양을 아는 유일한 곳
├─ core/
│   ├─ notify.py     Alert 모델과 발송 (채널 교체 가능)
│   ├─ state.py      중복 발송 방지 (LLM 판정층에서 재사용)
│   └─ config.py     YAML + ${ENV} 로딩
├─ sinks/
│   └─ ntfy.py       ntfy 푸시
└─ cli.py            test / run
docker-compose.yml   changedetection.io + sockpuppetbrowser
docs/adr/            설계 결정
```

### 3층 구조

1. **만능 폴백** — "이 페이지 이 영역이 바뀌면 알림". 어떤 URL 이든 즉시 작동.
2. **구조화 모듈** — 셀렉터·키워드·임계값을 아는 모듈. 아래 표.
3. **LLM 승격** — 주문을 받아 설정 초안을 만들고, `test_parse` 를 통과하면 2층으로. 실패하면 1층.

판정은 코드가 하고 LLM 은 접수와 서술만 한다.

## 알림 채널: 왜 ntfy 인가

카카오톡 "나에게 보내기"는 **푸시가 뜨지 않는다.** 직접 열어봐야 보이는 방식이라
몇 분이 갈리는 알림에는 쓸 수 없다. 알림톡과 친구톡은 사업자 등록과 심사가 필요해
개인에게는 막혀 있다.

ntfy는 계정 없이 앱 설치와 토픽 구독만으로 동작하고, `urgent` 우선순위로 보내면
무음·방해금지 모드를 뚫고 울린다. 알림을 탭하면 예매 페이지가 바로 열린다.

> ⚠️ **토픽 이름이 곧 비밀번호다.** 무료 공용 서버에서는 이름을 아는 사람 누구나
> 읽고 발행할 수 있다. 추측 불가능한 랜덤 문자열을 쓸 것.
>
> ```
> python -c "import secrets; print('watchog-' + secrets.token_urlsafe(9))"
> ```

## 설치

```bash
pip install -r requirements.txt
cp config/watch.example.yaml config/watch.yaml
cp .env.example .env            # 토픽을 새로 만들어 넣는다 (주석 참고)

docker compose up -d            # 감시 엔진. UI: http://localhost:5000
                                # Settings → API 의 키를 .env CDIO_API_KEY 에
python -m watchog test          # 폰에 알림이 뜨면 성공
```

폰에서는 ntfy 앱을 설치하고 `.env` 의 토픽을 구독한다. `sys` 토픽은 하트비트·에러용이라 항상 구독하고,
모듈 토픽은 받고 싶은 것만 구독한다 — **구독이 곧 범위 선택**이다.

## 실행 위치 — PC 에 남는 건 브라우저 감시 하나

| 어디서 | 무엇 | 명령 |
|---|---|---|
| GitHub Actions (`poll.yml`, 30분) | `runner: actions` 레시피 — 피드·requests 페이지 | `python -m watchog poll` |
| 로컬 Docker | `fetch: browser` 소스 (Google 특가, 영화관) | `docker compose up -d` + `recipes sync` |
| 로컬 작업 스케줄러 (08:40) | 하트비트 · 레시피 재검사 · 엔진 상태 | `python -m watchog ops daily` |

Actions 는 Settings → Secrets 에 `NTFY_TOPIC`·`NTFY_TOPIC_JOBS`·`NTFY_TOPIC_FLIGHT`·`NTFY_TOPIC_FEEDBACK` 을 넣기 전까지 dry-run 으로만 돈다.
하트비트는 `.env` 의 `HEALTHCHECK_URL`(healthchecks.io) 이 있을 때만 보낸다.

### Actions 메모

`data/seen.json` 은 **의도적으로 커밋된다.** Actions에는 영구 디스크가 없어서,
중복 방지 상태를 레포에 되돌려 커밋하는 것이 유일한 지속 수단이다.

> GitHub의 cron 최소 간격은 5분이고 부하에 따라 자주 지연된다. 분 단위 정확도가
> 필요하면 그 구간만 로컬이나 VPS로 돌리는 편이 낫다.

## 기본 모듈

| 모듈 | 상태 (2026-09-11) | 판정 | 비고 |
|---|---|---|---|
| movie (영화관) | 미등록 | item → threshold | 롯데시네마는 브라우저 렌더링 확인. 메가박스는 robots 전면 거부. CGV 는 [docs/cgv.md](docs/cgv.md) |
| jobs (채용) | **사람인 검색 3건 등록됨** (item) | 규칙 + LLM (2층 예정) | 잡코리아는 robots 가 검색 경로를 거부해 제외. 아래 참고 |
| flight (항공권) | **핫딜 감시 4건 등록됨** (Google Flights 특가 + 뽐뿌·루리웹 RSS·클리앙, "항공" 키워드) | 규칙 (키워드) | 구간 가격 추적은 하지 않는다. Google 은 브라우저 컨테이너 필수 |
| gamenews (게임뉴스) | 설계만 있음 | 규칙 + LLM | 구 MyPlayScheduler. 캘린더 동기화는 이때 다시 본다 |

### jobs: 왜 LLM이 필요한가

채용 사이트 필터로 걸러도 원하는 공고가 안 뜨는 이유는, *무엇을 원하는지가
키워드로 표현되지 않기* 때문이다. 그래서 캐스케이드로 간다.

```
수집 200건 → 규칙 필터 40건 → 중복 제거 5건 → LLM 판정 → 임계값 통과분만 푸시
```

LLM은 Gemini 무료 티어를 쓴다 (Flash 250 req/day, Flash-Lite 1,000 req/day).
중복 제거 뒤 실제 판정 물량은 하루 몇 건이라 무료 한도 안에서 끝난다.

> ⚠️ **이력서 원문을 프롬프트에 넣지 말 것.** 무료 티어는 입력이 학습에 쓰일 수
> 있다. 직무·스택·경력·지역 같은 **조건 목록으로 추상화**하면 판정 품질은 거의
> 그대로면서 개인 식별정보가 나가지 않는다.

## 코드네임

각 워처에는 표시용 코드네임이 붙는다. 파일명은 그대로 두어 가독성을 지키고,
알림 제목에만 쓴다 — 뭐가 울렸는지 한눈에 구분된다.

| 워처 | 코드네임 | 도감 |
|---|---|---|
| cgv | 🦉 noctowl | 정확히 같은 시각에 운다 |
| jobs | 🔮 xatu | 하루 종일 한 방향을 응시한다 |
| flight | 🕊 wingull | 바다 건너 먼 곳까지 난다 |
| gamenews | 🐦 chatot | 소리를 흉내 내어 옮긴다 |
