"""추출기 테스트. 실제 사이트 대신 고정 HTML/RSS 를 쓴다."""

import pytest

from watchog import extract

LIST_HTML = """
<html><head>
<link rel="alternate" type="application/rss+xml" href="/rss.xml">
</head><body>
<nav><ul class="menu"><li><a href="/a">홈</a></li><li><a href="/b">공지</a></li><li><a href="/c">문의</a></li>
<li><a href="/d">로그인</a></li><li><a href="/e">회원가입</a></li><li><a href="/f">고객센터</a></li></ul></nav>
<div class="list">
  <div class="row"><span><a href="/p/1">[효성ITX] AI MLOps 엔지니어 채용</a></span></div>
  <div class="row"><span><a href="/p/2">현대그린푸드 AI 데이터 엔지니어(MLOps)</a></span></div>
  <div class="row"><span><a href="/p/3">Forward Deployed Engineer (FDE) 모집</a></span></div>
  <div class="row"><span><a href="/p/4">AI 플랫폼 백엔드 개발자 채용 공고</a></span></div>
  <div class="row"><span><a href="/p/5">MLOps / AI 인프라 엔지니어 공고입니다</a></span></div>
  <div class="row"><span><a href="/p/6">데이터 플랫폼 엔지니어 경력 채용</a></span></div>
</div>
</body></html>
"""

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss><channel>
<item><title>[토스] 찰순대 400g (8,900원/무배)</title></item>
<item><title>[제주항공] 오사카 특가 항공권 오픈</title></item>
</channel></rss>
"""


def page(text, ct="text/html; charset=utf-8", url="https://example.com/list"):
    return extract.Fetched(url=url, status=200, content_type=ct, text=text)


def test_select_css_and_xpath():
    p = page(LIST_HTML)
    assert len(extract.select_items(p, "div.row a")) == 6
    assert extract.select_items(p, "xpath://div[@class='row']//a")[0].startswith("[효성ITX]")


def test_select_drops_numeric_only():
    p = page('<div class="t"><a href="/1">진짜 제목 하나</a><a href="/1#c">12</a><a href="/2">둘째 제목입니다</a><a href="/2#c">[3]</a></div>')
    assert extract.select_items(p, "div.t a") == ["진짜 제목 하나", "둘째 제목입니다"]


def test_select_rss_titles():
    p = page(RSS, ct="text/xml")
    items = extract.select_items(p, "xpath://item/title")
    assert items == ["[토스] 찰순대 400g (8,900원/무배)", "[제주항공] 오사카 특가 항공권 오픈"]


def test_propose_prefers_titles_over_menu():
    cands = extract.propose_selectors(page(LIST_HTML))
    assert cands, "후보가 하나는 나와야 한다"
    best = cands[0]
    assert best["count"] == 6
    assert "MLOps" in best["sample"][0]


def test_discover_feed_link():
    feeds = extract.discover_feeds(page(LIST_HTML))
    assert feeds == ["https://example.com/rss.xml"]


def test_extract_error_carries_category():
    e = extract.ExtractError("blocked", "no")
    assert e.category == "blocked"
    assert isinstance(e, RuntimeError)


def test_test_parse_uses_fixture(monkeypatch):
    monkeypatch.setattr(extract, "robots_allows", lambda url, agent="*": True)
    monkeypatch.setattr(extract, "fetch", lambda url, check_robots=True: page(LIST_HTML))
    r = extract.test_parse("https://example.com/list")
    assert r.ok and r.selector
    assert r.feeds == ["https://example.com/rss.xml"]
    assert any("피드" in n for n in r.notes)


def test_test_parse_blocked_by_robots(monkeypatch):
    monkeypatch.setattr(extract, "robots_allows", lambda url, agent="*": False)
    with pytest.raises(extract.ExtractError) as ei:
        extract.test_parse("https://example.com/list")
    assert ei.value.category == "blocked"


def test_test_parse_empty_selector(monkeypatch):
    monkeypatch.setattr(extract, "robots_allows", lambda url, agent="*": True)
    monkeypatch.setattr(extract, "fetch", lambda url, check_robots=True: page(LIST_HTML))
    with pytest.raises(extract.ExtractError) as ei:
        extract.test_parse("https://example.com/list", "div.nothing a")
    assert ei.value.category == "empty"
