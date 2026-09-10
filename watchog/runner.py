"""러너 — changedetection.io 없이 우리 코드만으로 레시피를 한 바퀴 돈다.

``runner: actions`` 레시피가 대상이다. GitHub Actions 처럼 상태를 못 남기는 곳에서 돌기 위해
seen-set(``data/seen.json``)을 레포에 되돌려 커밋하는 08-18 방식을 그대로 쓴다.
브라우저 렌더링(``fetch: browser``)은 여기서 못 하므로 레시피 검증 단계에서 이미 막혀 있다.

한 소스의 처리 순서:
    fetch → select → (trigger 필터) → seen-set 으로 새 항목만 → 이력 기록 → ntfy 발송

실패는 범주(blocked · empty · changed · down)로 나눠 sys 토픽에 보낸다 (fail-loud).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field

from watchog import extract
from watchog import history as history_mod
from watchog.core.notify import Alert, dispatch
from watchog.core.state import SeenStore
from watchog.recipes import Recipe, Source, resolve_topic

log = logging.getLogger(__name__)

MAX_ITEMS_PER_ALERT = 8


def item_id(text: str) -> str:
    """제목을 정규화해 해시로. 공백·기호·대소문자 차이는 같은 항목으로 본다 (중복 처리 1겹)."""
    norm = re.sub(r"[\W_]+", "", text).lower()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


@dataclass
class SourceResult:
    recipe: str
    index: int
    title: str
    new_items: list[str] = field(default_factory=list)
    error: str | None = None
    category: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _sinks_for(topic_key: str) -> dict:
    """레시피 토픽으로 ntfy sink 설정을 만든다."""
    return {"ntfy": {"topic": resolve_topic(topic_key)}}


def run_source(recipe: Recipe, index: int, src: Source, seen: SeenStore, *, dry_run: bool = False) -> SourceResult:
    label = src.title or src.url
    result = SourceResult(recipe=recipe.name, index=index, title=label)
    if src.type not in ("feed", "web"):
        result.error, result.category = f"러너가 아직 지원하지 않는 소스 종류: {src.type}", "changed"
        return result
    if src.fetch == "browser":
        result.error, result.category = "브라우저 렌더링 소스는 changedetection.io(로컬) 몫입니다", "changed"
        return result
    try:
        page = extract.fetch(src.url)
        selector = src.select[0] if src.select else None
        if selector:
            items = extract.select_items(page, selector)
        elif page.is_xml:
            items = extract.select_items(page, "xpath://item/title")
        else:
            # diff 종류: 본문 텍스트 전체를 항목 하나로 본다.
            items = [extract._clean(extract.html.fromstring(page.text).text_content())[: extract.MAX_TEXT]]
    except extract.ExtractError as e:
        result.error, result.category = str(e), e.category
        return result
    if not items:
        result.error, result.category = "잡히는 항목이 0건 — 셀렉터가 깨졌을 수 있습니다", "empty"
        return result
    if src.trigger:
        items = [t for t in items if any(k in t for k in src.trigger)]
    namespace = f"{recipe.name}/{index}"
    fresh = seen.filter_new(namespace, items, item_id)
    result.new_items = fresh
    if not dry_run:
        for t in fresh:
            seen.mark(namespace, item_id(t))
    return result


def notify(recipe: Recipe, result: SourceResult, *, dry_run: bool = False) -> None:
    """새 항목을 한 통으로 보낸다. mode: digest 는 발송기가 생길 때까지 즉시 발송과 같다."""
    if not result.new_items:
        return
    shown = result.new_items[:MAX_ITEMS_PER_ALERT]
    body = "\n".join(f"• {t[:90]}" for t in shown)
    if len(result.new_items) > MAX_ITEMS_PER_ALERT:
        body += f"\n… 외 {len(result.new_items) - MAX_ITEMS_PER_ALERT}건"
    src = recipe.sources[result.index]
    alert = Alert(
        title=f"[{recipe.topic}] {result.title} · 새 항목 {len(result.new_items)}건",
        body=body,
        url=src.url,
        priority="default",
        tags=["bell"],
        source=recipe.name,
    )
    if dry_run:
        log.info("(dry-run) would send: %s\n%s", alert.title, alert.body)
        return
    # 이력에 먼저 남기고, 그 id 로 「유용 / 무시」 버튼을 붙인다.
    with history_mod.History() as h:
        alert_id = h.record(recipe=recipe.name, source_index=result.index, title=alert.title, body=alert.body,
                            url=alert.url, topic=recipe.topic, priority=alert.priority,
                            item_ids=[item_id(t) for t in result.new_items])
    alert.actions = history_mod.feedback_actions(alert_id)
    dispatch(alert, _sinks_for(recipe.topic))


def notify_failure(recipe: Recipe, result: SourceResult, *, dry_run: bool = False) -> None:
    alert = Alert(
        title=f"[{result.category}] {recipe.name}/{result.index} {result.title}",
        body=result.error or "",
        url=recipe.sources[result.index].url,
        priority="low",
        tags=["warning"],
        source="runner",
    )
    if dry_run:
        log.warning("(dry-run) would report: %s — %s", alert.title, alert.body)
        return
    dispatch(alert, _sinks_for("sys"))


def run(recipes_list: list[Recipe], *, state_path: str = "data/seen.json", retention_days: int = 90,
        only_runner: str | None = "actions", dry_run: bool = False) -> list[SourceResult]:
    """레시피들을 한 바퀴. only_runner 로 대상 러너를 고른다 (None 이면 전부)."""
    results: list[SourceResult] = []
    targets = [r for r in recipes_list if only_runner is None or r.runner == only_runner]
    if not targets:
        log.info("runner=%s 인 레시피가 없습니다", only_runner)
        return results
    with SeenStore(state_path, retention_days) as seen:
        for r in targets:
            if not r.is_verified:
                log.warning("레시피 %s 는 verified 가 없어 건너뜁니다", r.name)
                continue
            for i, src in enumerate(r.sources):
                res = run_source(r, i, src, seen, dry_run=dry_run)
                results.append(res)
                if res.ok:
                    log.info("%s/%d %s: 새 항목 %d건", r.name, i, res.title, len(res.new_items))
                    try:
                        notify(r, res, dry_run=dry_run)
                    except Exception:
                        log.exception("발송 실패 %s/%d", r.name, i)
                else:
                    log.warning("%s/%d %s: [%s] %s", r.name, i, res.title, res.category, res.error)
                    try:
                        notify_failure(r, res, dry_run=dry_run)
                    except Exception:
                        log.exception("실패 보고도 실패 %s/%d", r.name, i)
        if dry_run:
            seen._dirty = False  # dry-run 은 상태를 남기지 않는다
    return results


def first_run_seeds(recipes_list: list[Recipe], state_path: str = "data/seen.json") -> int:
    """처음 켤 때 현재 목록을 전부 '본 것'으로 적어, 첫 실행에서 수십 건이 한꺼번에 울리지 않게 한다."""
    n = 0
    with SeenStore(state_path) as seen:
        for r in recipes_list:
            for i, src in enumerate(r.sources):
                res = run_source(r, i, src, seen)
                n += len(res.new_items)
    return n


if __name__ == "__main__":  # python -m watchog.runner
    from dotenv import find_dotenv, load_dotenv

    from watchog import recipes as recipes_mod

    load_dotenv(find_dotenv(usecwd=True))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
    dry = os.environ.get("WATCHOG_DRY_RUN") == "1"
    run(recipes_mod.load_all(), dry_run=dry)
