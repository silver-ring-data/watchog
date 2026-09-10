"""1주차 수동 등록 (python scripts/register_week1.py): 채용(잡코리아 검색)."""
import os, sys, time, urllib.parse
from pathlib import Path

# 레포 루트에서 실행하지 않아도 watchog 패키지를 찾도록 한다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(usecwd=True))
from watchog import cdio

c = cdio.ChangeDetection.from_env()

# 1) 채용 — 잡코리아 검색 결과. 클래스가 Tailwind 유틸이라 셀렉터 대신
#    공고 링크(href 에 GI_Read) 만 뽑아 제목 목록의 diff 를 본다.
jobs_topic = os.environ["NTFY_TOPIC_JOBS"]
for kw in ["MLOps", "Forward Deployed", "AI 플랫폼"]:
    url = "https://www.jobkorea.co.kr/Search/?stext=" + urllib.parse.quote(kw)
    uuid = c.add_watch(
        url,
        title=f"[jobs] 잡코리아 · {kw}",
        tag="jobs",
        notification_urls=[cdio.ntfy_url(jobs_topic)],
        time_between_check=cdio.interval(hours=1),
        fetch_backend=cdio.FETCH_REQUESTS,
        include_filters=['xpath://a[contains(@href,"/Recruit/GI_Read/")]'],
    )
    print("jobs", kw, uuid)

# 항공권은 scripts/register_flight.py 에서 등록한다.

print("watch_count", c.system_info()["watch_count"])
