"""항공권(flight) 모듈 1층 등록 (python scripts/register_flight.py).

특정 구간 가격이 아니라 **핫딜이 뜨면** 알리는 구성이다 (2026-09-11 결정).
- Google Flights 특가: 서울발 특가 30건 목록. 브라우저 fetcher 필요.
- 커뮤니티 핫딜 피드: 제목 목록을 보되 바뀐 줄에 "항공"이 있을 때만 알림(trigger_text).
  include_filters 로 "항공"만 뽑으면 해당 글이 없는 시점에 필터 실패 오류가 나므로 쓰지 않는다.
"""
import os, sys
from pathlib import Path

# 레포 루트에서 실행하지 않아도 watchog 패키지를 찾도록 한다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(usecwd=True))
from watchog import cdio

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
c = cdio.ChangeDetection.from_env()
topic = os.environ["NTFY_TOPIC_FLIGHT"]
notify = [cdio.ntfy_url(topic)]

# 이미 등록된 URL 은 건너뛴다 (재실행 안전).
existing = {w.get("url"): u for u, w in c.list_watches().items()}


def ensure(url, **kw):
    if url in existing:
        c.update_watch(existing[url], notification_urls=notify, tag="flight", **{k: v for k, v in kw.items() if k in ("title", "time_between_check")})
        print("updated", kw.get("title"))
        return existing[url]
    u = c.add_watch(url, tag="flight", notification_urls=notify, **kw)
    print("added  ", kw.get("title"))
    return u


ensure(
    "https://www.google.com/travel/flights/deals?hl=ko",
    title="[flight] Google Flights · 서울발 특가",
    time_between_check=cdio.interval(hours=12),
    fetch_backend=cdio.FETCH_BROWSER,
)

feeds = {
    "뽐뿌게시판": "https://www.ppomppu.co.kr/rss.php?id=ppomppu",
    "루리웹 핫딜": "https://bbs.ruliweb.com/market/board/1020/rss",
}
for name, url in feeds.items():
    ensure(
        url,
        title=f"[flight] {name} RSS · 항공",
        time_between_check=cdio.interval(hours=1),
        fetch_backend=cdio.FETCH_REQUESTS,
        include_filters=["xpath://item/title"],
        trigger_text=["항공"],
    )

ensure(
    "https://www.clien.net/service/board/jirum",
    title="[flight] 클리앙 알뜰구매 · 항공",
    time_between_check=cdio.interval(hours=1),
    fetch_backend=cdio.FETCH_REQUESTS,
    include_filters=["span.list_subject"],
    trigger_text=["항공"],
)

print("watch_count", c.system_info()["watch_count"])
