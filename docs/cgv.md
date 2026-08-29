# CGV 상영시간표 조사 기록

2026-08-18 조사. 인터넷에 도는 CGV 크롤링 코드는 **전부 옛날 것**이라 그대로 쓰면
동작하지 않는다. 아래는 직접 확인한 현재 상태.

## 1. 메인 사이트는 Next.js로 재구축됐다

`www.cgv.co.kr` → `cgv.co.kr` 로 이전됐고 Next.js App Router SPA다.
`/common/showtimes/iframeTheater.aspx` 같은 구 엔드포인트는 **경로와 무관하게
SPA 셸 HTML을 돌려준다**. 404도 아니고 200에 빈 껍데기라, 파싱이 조용히 실패한다.

정적 CDN: `https://cdn.cgv.co.kr/cgvpomscontent/static/script/<build>/_next/...`

## 2. 신규 API와 BFF 매핑

프론트는 자기 오리진의 `/api/v1/*` 를 호출하고, 그게 `api.cgv.co.kr` 로 프록시된다.
`api.cgv.co.kr` 직접 호출은 **401**, BFF 경유는 인증 없이 백엔드까지 도달한다.

JS 청크에서 추출한 매핑 테이블:

| BFF (cgv.co.kr) | 백엔드 (api.cgv.co.kr) |
|---|---|
| `/api/v1/booking` | `/cnm/atkt` |
| `/api/v1/content` | `/cnm` |
| `/api/v1/common` | `/com` |
| `/api/v1/member` | `/mem` |
| `/api/v1/mypage` | `/mcv` |
| `/api/v1/payment` | `/mpy` |
| `/api/v1/activity` | `/act` |
| `/api/v1/store` | `/sto` |
| `/api/v1/support` | `/csc` |
| `/api/v1/content/event` | `/evt` |

신규 API의 필드명은 **평문**이다:

```
coCd "A420"   회사코드      siteNo   극장번호
scnYmd        상영년월일    scnsNo   상영회차번호
movNo / movNm 영화번호/명   siteScnsNm 상영관명
scnsTm        상영시각      scnSseq
```

**미해결**: 예매 플로우의 정확한 엔드포인트 접미사를 못 찾았다.
`/ticket` `/booking` `/reservation` `/movies` 등 라우트는 전부 404고,
`/api/v1/booking/{schedule,theaterList,...}` 추측도 전부
`No endpoint ... /cnm/atkt/xxx` 를 반환했다.
→ **실제 브라우저로 예매 화면을 한 번 돌면서 네트워크를 캡처하면 바로 확정된다.**

## 3. 레거시 예매 백엔드는 아직 살아있다

`ticket.cgv.co.kr` 의 구 ASP.NET 시스템이 그대로 동작한다.

```
POST http://ticket.cgv.co.kr/CGV2011/RIA/CJ000.aspx/CJ_TICKET_SCHEDULE_TOTAL_PLAY_YMD
Content-Type: application/json
X-Requested-With: XMLHttpRequest
Referer: http://ticket.cgv.co.kr/Reservation/Reservation.aspx?...
```

### 함정: 파라미터가 암호화돼 있다

```json
{
  "REQSITE":        "x02PG4EcdFrHKluSEQQh4A==",
  "TheaterCd":      "LMP+XuzWskJLFG41YQ7HGA==",
  "MovieGroupCd":   "bNQovwyoamC5EsbGvSDIqw==",
  "ScreenRatingCd": "nG6tVgEQPGU2GvOIdnwTjg==",
  "MovieTypeCd":    "/Saxvehmz4RPKZDKNMvSKQ=="
}
```

- 같은 평문 → 같은 암호문이다. `ScreenRatingCd`, `Subtitle_CD`, `SOUNDX_YN`,
  `Third_Attr_CD` 가 모두 `nG6tVgEQPGU2GvOIdnwTjg==` 로 동일한데, 이건 "빈 값"의
  암호문이다. 즉 **결정적(deterministic) 모드**라 값을 한 번 수집해두면 재사용된다.
- 대신 **임의의 극장/영화 코드를 만들어낼 수는 없다.** 키가 없으면 조합 불가.

이 때문에 기존 오픈소스([cgv-open-push](https://github.com/0w0i0n0g0/cgv-open-push))는
용산 IMAX / 여의도 4DX 등 **고정된 목록만** 지원한다. 브라우저에서 값을 떠다
하드코딩한 구조다.

### 그래서 우리의 선택지

**복호화는 필요 없다.** 암호화된 코드를 *불투명 ID* 로 취급하고, 사이트가 제공하는
목록 응답에서 그대로 수확해 드롭다운을 채우면 된다. 사용자는 "용산아이파크몰"을
고르고, 내부적으로는 대응하는 블롭을 실어 보낸다.
→ 목록 엔드포인트를 찾는 게 관건이며, 이것도 브라우저 네트워크 캡처로 해결된다.

## 4. robots.txt

Cloudflare 앞단. 규칙이 서로 모순되게 겹쳐 있다.

```
User-agent: *
Content-Signal: search=yes,ai-train=no,use=reference
Allow: /

User-agent: *
Disallow: /            ← 일반 봇 전면 차단

User-agent: Googlebot / NaverBot / Yeti / Bingbot / Daumoa ...
Crawl-delay: 5
Allow: /               ← 지정된 검색엔진만 허용
```

읽는 그대로는 **지명된 검색엔진 외 자동 접근을 원하지 않는다**는 의사 표시다.
개인용 저빈도 조회가 곧바로 위법인 것은 아니지만, 명시된 의사인 만큼
판단하고 넘어가야 할 사안이다.

## 5. CGV 공식 알림이 이미 있다 (부분적으로)

CGV는 **상영시간표 오픈 시 카카오톡 알림톡**을 공식 제공한다.
API에도 `/api/v1/content/event/saprm/saprm/sendKakaoAlarm` 이 존재한다.

- 기준: **영화 + 날짜**
- 최대 **3개**까지 신청

즉 "오픈됐다"까지는 공식으로 커버되지만,
**극장별 / 상영관별(IMAX·4DX) / 시간대별 조건은 지원하지 않는다.**
watchog가 채우려는 공백이 정확히 여기다.

## 6. 남은 작업

1. Playwright로 예매 플로우 1회 실행하며 네트워크 캡처
   → 신규 API 엔드포인트 또는 레거시 목록 엔드포인트 확정
2. 극장/영화/상영관 목록을 수확해 캐시 (드롭다운 소스)
3. 확정된 엔드포인트로 가벼운 `requests` 폴링 구현
4. 폴링 간격은 보수적으로. 오픈 임박 시에만 좁힌다.
