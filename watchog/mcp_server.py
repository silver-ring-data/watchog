"""watchog MCP 서버 — Claude 가 앞단이 되는 접수 창구.

Claude 는 사용자의 말에서 접수 양식 다섯 칸(무엇 · 종류 · 어디서 · 얼마나 · 조건)을 채우고
아래 도구를 부른다. 판정은 코드가 한다: 소스가 막혔는지(robots·403), 셀렉터가 실제로
항목을 뽑는지(test_parse)를 코드가 확인하고, 통과한 것만 ``verified`` 가 붙어 등록된다.

실행:  python -m watchog.mcp_server           (stdio)
등록:  claude mcp add watchog -- python -m watchog.mcp_server   (레포 루트에서)

도구:
    list_watches()                         등록된 레시피와 소스, changedetection.io 상태
    test_parse(url, selector?)             등록 전 미리보기 — 무엇이 잡히는지, 후보 셀렉터, 피드, 막힘 여부
    add_watch(name, topic, kind, url, ...) 레시피 초안 → 검증 → 저장 → 동기화
    remove_watch(name)                     레시피 삭제 + 감시 해제
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from mcp.server.mcpserver import MCPServer

from watchog import extract, recipes
from watchog.recipes import Recipe, RecipeError, Source

load_dotenv(find_dotenv(usecwd=True))

mcp = MCPServer(
    "watchog",
    instructions=(
        "watchog 는 개인용 알림 허브다. 사용자가 '○○ 알람 받고 싶어'라고 하면 다섯 칸을 채워라: "
        "무엇(name·topic) · 종류(kind: 뉴스/새 항목=item, 페이지 변화=diff, 가격·오픈 조건=threshold, 일정=event) · "
        "어디서(url, 모르면 후보를 찾아 제안) · 얼마나(every, mode: instant|digest) · 조건(threshold 만). "
        "등록 전에 반드시 test_parse 로 무엇이 잡히는지 보여주고 확인을 받아라. "
        "막힌 소스(blocked)는 뚫지 말고 대안(피드·다른 사이트·이메일 알림)을 제안하라. "
        "'소식 다' 같은 요청은 뉴스(item·digest)와 일정(event) 둘로 갈라 제안하라. "
        "예: '리버풀 뉴스 하루 두 번 묶어서' → kind=item, mode=digest, every=12h. "
        "'ICN-NRT 15만원 이하면 바로' → kind=threshold (아직 미구현이라 안내만). "
        "'이 페이지 바뀌면' → kind=diff."
    ),
)


def _recipes_dir() -> Path:
    return Path(os.environ.get("WATCHOG_RECIPES", "recipes"))


def _client():
    from watchog.cdio import ChangeDetection

    return ChangeDetection.from_env()


def _fmt_items(items: list[str], limit: int = 12) -> str:
    shown = "\n".join(f"  - {t[:100]}" for t in items[:limit])
    more = f"\n  … 외 {len(items) - limit}건" if len(items) > limit else ""
    return shown + more


@mcp.tool()
def list_watches() -> str:
    """등록된 레시피와 각 소스, changedetection.io 쪽 상태(마지막 확인·오류)를 보여준다."""
    found = recipes.load_all(_recipes_dir())
    if not found:
        return "등록된 레시피가 없습니다."
    status: dict[str, dict] = {}
    try:
        for _uuid, w in _client().list_watches().items():
            title = w.get("title") or ""
            m = re.match(r"^([a-z0-9][a-z0-9-]*/\d+) · ", title)
            if m:
                status[m.group(1)] = w
    except Exception as e:  # 엔진이 꺼져 있어도 목록은 보여준다
        status_note = f"(changedetection.io 상태를 읽지 못했습니다: {e})"
    else:
        status_note = ""
    lines = []
    for r in found:
        lines.append(f"{r.name}  [{r.topic}] {r.kind}/{r.mode} runner={r.runner} verified={r.verified}")
        for i, s in enumerate(r.sources):
            w = status.get(r.watch_key(i), {})
            err = w.get("last_error")
            state = "오류: " + str(err)[:60] if err else ("등록됨" if w else "미등록")
            lines.append(f"   {i}. {s.type:<6} {s.every:<4} {s.fetch:<8} {s.title or s.url}  — {state}")
    if status_note:
        lines.append(status_note)
    return "\n".join(lines)


@mcp.tool()
def test_parse(url: str, selector: str | None = None) -> str:
    """등록 전 미리보기. robots.txt 를 확인하고, 셀렉터가 없으면 후보를 제안하며, 무엇이 잡히는지 보여준다.

    selector 는 CSS("div.list a") 또는 XPath("xpath://item/title"). 막힌 소스면 [blocked] 로 답한다.
    """
    try:
        res = extract.test_parse(url, selector)
    except extract.ExtractError as e:
        hint = {
            "blocked": "이 소스는 자동 접근을 거부합니다. 뚫지 않습니다. 대안: 같은 사이트의 RSS/API, 다른 사이트, 이메일 알림.",
            "empty": "셀렉터로 잡히는 항목이 없습니다. selector 없이 다시 부르면 후보를 제안합니다.",
            "down": "사이트에 연결하지 못했습니다. 잠시 뒤 다시 시도하세요.",
            "changed": "페이지 구조를 읽지 못했습니다.",
        }.get(e.category, "")
        return f"[{e.category}] {e}\n{hint}"
    out = [f"url: {res.url}", f"selector: {res.selector or '(없음 — 1층 diff 로 등록 가능)'}"]
    for n in res.notes:
        out.append(f"note: {n[:300]}")
    if res.candidates:
        out.append("셀렉터 후보 (번호로 고르세요):")
        for i, c in enumerate(res.candidates):
            out.append(f"  {i}. {c['selector']}  ({c['count']}건)  예: {' / '.join(c['sample'])[:100]}")
    if res.items:
        out.append(f"잡힌 항목 {len(res.items)}건:")
        out.append(_fmt_items(res.items))
    return "\n".join(out)


@mcp.tool()
def add_watch(
    name: str,
    topic: str,
    kind: str,
    url: str,
    title: str = "",
    selector: str | None = None,
    trigger: list[str] | None = None,
    every: str = "1h",
    mode: str = "instant",
    digest_every: str = "6h",
    fetch: str = "requests",
    source_type: str = "web",
    confirm: bool = False,
) -> str:
    """레시피를 만들어 등록한다. 검증(test_parse)을 통과해야만 verified 가 붙고 등록된다.

    name: 소문자·숫자·하이픈 (예: liverpool-news). topic: ntfy 토픽 키 (sys/jobs/flight/movie 또는 새 이름 —
    새 이름이면 .env 에 NTFY_TOPIC_<이름> 이 있어야 한다). kind: diff|item|threshold|event.
    fetch: requests|browser (browser 는 JS 렌더링, 로컬 엔진에서만). source_type: web|feed.
    confirm=False 면 미리보기만 하고 저장하지 않는다. 사용자가 미리보기를 보고 OK 한 뒤 confirm=True 로 다시 부른다.
    """
    if kind in ("threshold", "event"):
        return f"kind={kind} 는 아직 판정층이 없어 등록할 수 없습니다 (로드맵 4주차). 지금은 diff 나 item 으로 등록하고 나중에 승격하세요."
    try:
        recipes.resolve_topic(topic)
    except RecipeError as e:
        return f"토픽 오류: {e}. .env 에 {recipes.topic_env_name(topic)} 를 추가한 뒤 다시 시도하세요."

    # 검증 — browser 는 로컬 엔진이 렌더링하므로 여기서는 robots 만 본다.
    preview = ""
    if fetch == "browser":
        if extract.robots_allows(url) is False:
            return f"[blocked] robots.txt 가 자동 접근을 거부합니다: {url}. 뚫지 않습니다."
        preview = "(브라우저 렌더링 소스라 미리보기는 등록 후 엔진에서 확인합니다)"
    else:
        try:
            res = extract.test_parse(url, selector)
        except extract.ExtractError as e:
            return f"[{e.category}] {e}\n등록하지 않았습니다."
        if kind == "item" and not res.items:
            return "item 종류인데 잡히는 항목이 없습니다. kind=diff 로 등록하거나 selector 를 지정하세요.\n" + "\n".join(res.notes[:2])
        selector = selector or res.selector
        preview = f"selector: {selector}\n잡힌 항목 {len(res.items)}건:\n{_fmt_items(res.items)}"
        if res.feeds and source_type == "web":
            preview += f"\nnote: 이 페이지는 피드를 제공합니다 — {res.feeds[0]} 로 등록하는 편이 안정적입니다."

    src = Source(type=source_type, url=url, title=title, fetch=fetch,
                 select=[selector] if selector else [], trigger=list(trigger or []), every=every)
    recipe = Recipe(name=name, topic=topic, kind=kind, mode=mode, digest_every=digest_every,
                    runner="local", sources=[src], verified=date.today())
    try:
        recipe.validate()
    except RecipeError as e:
        return f"레시피 오류: {e}"

    if not confirm:
        return f"미리보기 (저장 안 함)\n{preview}\n\n레시피:\n{recipes.dump(recipe)}\n이대로 등록하려면 confirm=True 로 다시 부르세요."

    path = recipes.save(recipe, _recipes_dir())
    try:
        result = recipes.sync(recipes.load_all(_recipes_dir()), _client())
    except Exception as e:
        return f"레시피는 저장했지만({path}) changedetection.io 동기화에 실패했습니다: {e}\n엔진을 켜고 `python -m watchog recipes sync` 를 실행하세요."
    return f"등록했습니다: {path}\n동기화: {result}\n{preview}"


@mcp.tool()
def remove_watch(name: str) -> str:
    """레시피 파일을 지우고 changedetection.io 의 해당 감시도 해제한다."""
    path = _recipes_dir() / f"{name}.yaml"
    if not path.exists():
        return f"레시피 {name} 이 없습니다."
    path.unlink()
    try:
        result = recipes.sync(recipes.load_all(_recipes_dir()), _client())
    except Exception as e:
        return f"레시피는 지웠지만 동기화에 실패했습니다: {e}"
    return f"삭제했습니다: {name}\n동기화: {result}"


if __name__ == "__main__":
    mcp.run()
