"""가져오기 · 뽑기 · 검증 — test_parse 와 Actions 러너가 같이 쓰는 층.

LLM 에 페이지를 통째로 주지 않기 위한 코드다. 순서는 늘 "코드가 먼저":

1. robots.txt 를 읽어 거부면 ``blocked`` 로 돌린다 (막힌 소스는 뚫지 않는다).
2. 피드 자동 발견 — ``<link rel="alternate" type=".../rss+xml">`` 과 흔한 경로를 시도한다.
3. 셀렉터로 항목을 뽑는다. 없으면 반복 구조(같은 부모 아래 링크가 많은 영역)에서 후보 셀렉터를
   최대 3개 제안한다. LLM 은 이 중 번호 하나를 고를 뿐 셀렉터를 지어내지 않는다.

브라우저 렌더링이 필요한 페이지는 changedetection.io 몫이라 여기서는 requests 만 쓴다.
"""

from __future__ import annotations

import re
import urllib.robotparser
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import requests
from lxml import etree, html

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) watchog/1.0 (+https://github.com/silver-ring-data/watchog)"
TIMEOUT = 20
FEED_PATHS = ("/rss", "/feed", "/rss.xml", "/feed.xml", "/atom.xml", "/index.xml")
MAX_ITEMS = 60          # 미리보기·LLM 입력 상한
MAX_TEXT = 3000         # LLM 에 넘길 때 글자 상한


class ExtractError(RuntimeError):
    """범주가 붙은 실패. category 는 blocked | login | empty | changed | down 중 하나."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


@dataclass
class Fetched:
    url: str
    status: int
    content_type: str
    text: str

    @property
    def is_xml(self) -> bool:
        ct = self.content_type
        return "xml" in ct or self.text.lstrip()[:5] in ("<?xml", "<rss ", "<feed")


@dataclass
class ParseResult:
    url: str
    items: list[str]
    selector: str | None
    candidates: list[dict] = field(default_factory=list)   # {"selector", "count", "sample"}
    feeds: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.items)


# ── 1. robots ────────────────────────────────────────────────────────────


def robots_allows(url: str, agent: str = "*") -> bool | None:
    """허용이면 True, 거부면 False, robots 를 못 읽으면 None (판단 유보)."""
    parts = urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    try:
        resp = requests.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    except requests.RequestException:
        return None
    if resp.status_code >= 400:
        return None
    rp.parse(resp.text.splitlines())
    return rp.can_fetch(agent, url)


# ── 2. 가져오기 ──────────────────────────────────────────────────────────


def fetch(url: str, *, check_robots: bool = True) -> Fetched:
    if check_robots and robots_allows(url) is False:
        raise ExtractError("blocked", f"robots.txt 가 자동 접근을 거부합니다: {url}")
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise ExtractError("down", f"연결 실패: {e}") from e
    if resp.status_code in (401, 403):
        raise ExtractError("blocked", f"HTTP {resp.status_code} — 봇 차단이거나 로그인이 필요합니다: {url}")
    if resp.status_code >= 500:
        raise ExtractError("down", f"HTTP {resp.status_code}: {url}")
    if resp.status_code >= 400:
        raise ExtractError("down", f"HTTP {resp.status_code}: {url}")
    if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding
    return Fetched(url=resp.url, status=resp.status_code,
                   content_type=resp.headers.get("content-type", "").lower(), text=resp.text)


# ── 3. 피드 자동 발견 ────────────────────────────────────────────────────


def discover_feeds(page: Fetched, *, probe: bool = False) -> list[str]:
    """페이지의 <link rel=alternate> 피드와, probe=True 면 흔한 경로까지 시도한다."""
    found: list[str] = []
    if not page.is_xml:
        try:
            doc = html.fromstring(page.text)
        except (etree.ParserError, ValueError):
            doc = None
        if doc is not None:
            for link in doc.xpath('//link[@rel="alternate"]'):
                t = (link.get("type") or "").lower()
                if "rss" in t or "atom" in t or "xml" in t:
                    href = link.get("href")
                    if href:
                        found.append(urljoin(page.url, href))
    if probe:
        base = "{0.scheme}://{0.netloc}".format(urlsplit(page.url))
        for path in FEED_PATHS:
            cand = base + path
            if cand in found:
                continue
            try:
                r = requests.get(cand, headers={"User-Agent": USER_AGENT}, timeout=10)
            except requests.RequestException:
                continue
            if r.status_code == 200 and ("xml" in r.headers.get("content-type", "").lower()
                                         or r.text.lstrip()[:5] in ("<?xml", "<rss ", "<feed")):
                found.append(cand)
    # 순서 유지 중복 제거
    seen: set[str] = set()
    return [f for f in found if not (f in seen or seen.add(f))]


# ── 4. 항목 뽑기 ─────────────────────────────────────────────────────────


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


def select_items(page: Fetched, selector: str) -> list[str]:
    """셀렉터(``xpath:...`` 또는 CSS)로 텍스트 항목을 뽑는다. changedetection.io 와 같은 규칙."""
    if page.is_xml:
        try:
            root = etree.fromstring(page.text.encode("utf-8"), parser=etree.XMLParser(recover=True))
        except (etree.XMLSyntaxError, ValueError) as e:
            raise ExtractError("changed", f"XML 파싱 실패: {e}") from e
    else:
        try:
            root = html.fromstring(page.text)
        except (etree.ParserError, ValueError) as e:
            raise ExtractError("changed", f"HTML 파싱 실패: {e}") from e
    if selector.startswith("xpath:"):
        nodes = root.xpath(selector[len("xpath:"):])
    else:
        nodes = root.cssselect(selector)
    items = []
    for n in nodes:
        text = n if isinstance(n, str) else "".join(n.itertext())
        text = _clean(text)
        if text:
            items.append(text)
    return items[:MAX_ITEMS]


def _css_path(el) -> str:
    """요소를 가리키는 짧은 CSS 셀렉터. 클래스가 있으면 태그.클래스, 없으면 부모 태그 > 태그."""
    tag = el.tag if isinstance(el.tag, str) else "*"
    cls = (el.get("class") or "").split()
    cls = [c for c in cls if re.fullmatch(r"[A-Za-z][\w-]{1,30}", c)][:2]
    if cls:
        return tag + "".join(f".{c}" for c in cls)
    parent = el.getparent()
    if parent is not None and isinstance(parent.tag, str):
        return f"{parent.tag} > {tag}"
    return tag


def propose_selectors(page: Fetched, *, limit: int = 3) -> list[dict]:
    """반복 구조를 찾아 후보 셀렉터를 제안한다. 목록 페이지의 "제목 링크" 를 노린다.

    같은 (부모 경로, 링크 셀렉터) 조합으로 텍스트 있는 링크가 여러 개 모인 곳이 후보다.
    """
    if page.is_xml:
        return [{"selector": "xpath://item/title", "count": len(select_items(page, "xpath://item/title")),
                 "sample": select_items(page, "xpath://item/title")[:3]}]
    try:
        doc = html.fromstring(page.text)
    except (etree.ParserError, ValueError):
        return []
    groups: Counter = Counter()
    samples: dict[str, list[str]] = {}
    for a in doc.xpath("//a[@href]"):
        text = _clean(a.text_content())
        if len(text) < 4 or len(text) > 120:
            continue
        parent = a.getparent()
        grand = parent.getparent() if parent is not None else None
        if grand is None:
            continue
        key = f"{_css_path(grand)} {_css_path(a)}"
        groups[key] += 1
        samples.setdefault(key, [])
        if len(samples[key]) < 3:
            samples[key].append(text)
    # 후보 점수 = 개수 × 평균 길이 × 고유 비율. 메뉴·탭은 짧고 겹쳐서 밀려나고,
    # 공고·게시글 제목처럼 길고 제각각인 목록이 앞으로 온다.
    scored = []
    for key, count in groups.items():
        if count < 5:
            continue
        try:
            items = select_items(page, key)
        except Exception:
            continue
        if len(items) < 5:
            continue
        avg_len = sum(len(t) for t in items) / len(items)
        uniq = len(set(items)) / len(items)
        if avg_len < 8 or uniq < 0.6:
            continue
        scored.append((len(items) * avg_len * uniq, key, items))
    scored.sort(reverse=True)
    return [{"selector": key, "count": len(items), "sample": items[:3]} for _, key, items in scored[:limit]]


def test_parse(url: str, selector: str | None = None, *, check_robots: bool = True) -> ParseResult:
    """등록 전 검증. 무엇이 잡히는지 보여주고, 셀렉터가 없으면 후보를 제안한다."""
    notes: list[str] = []
    allowed = robots_allows(url) if check_robots else None
    if allowed is False:
        raise ExtractError("blocked", f"robots.txt 가 자동 접근을 거부합니다: {url}")
    if allowed is None and check_robots:
        notes.append("robots.txt 를 읽지 못해 허용 여부를 판단하지 않았습니다")
    page = fetch(url, check_robots=False)
    feeds = discover_feeds(page)
    if feeds:
        notes.append(f"이 페이지는 피드를 제공합니다 — 피드가 더 안정적입니다: {feeds[0]}")

    if selector:
        items = select_items(page, selector)
        if not items:
            raise ExtractError("empty", f"셀렉터 {selector!r} 로 잡히는 항목이 없습니다")
        return ParseResult(url=page.url, items=items, selector=selector, feeds=feeds, notes=notes)

    if page.is_xml:
        items = select_items(page, "xpath://item/title") or select_items(page, "xpath://*[local-name()='entry']/*[local-name()='title']")
        if items:
            return ParseResult(url=page.url, items=items, selector="xpath://item/title", feeds=feeds, notes=notes)
    cands = propose_selectors(page)
    if cands:
        best = cands[0]
        notes.append(f"셀렉터 후보 {len(cands)}개 중 첫째를 임시로 썼습니다. 다른 것이 맞으면 번호로 고르세요")
        return ParseResult(url=page.url, items=select_items(page, best["selector"]),
                           selector=best["selector"], candidates=cands, feeds=feeds, notes=notes)
    # 후보도 없으면 1층(diff) 로 폴백 — 본문 텍스트만 돌려준다.
    body = _clean(html.fromstring(page.text).text_content()) if not page.is_xml else page.text
    notes.append("목록 구조를 찾지 못했습니다. 1층(페이지 변화 감시) 로 등록할 수 있습니다")
    return ParseResult(url=page.url, items=[], selector=None, feeds=feeds, notes=notes + [body[:MAX_TEXT]])
