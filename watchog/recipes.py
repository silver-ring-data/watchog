"""레시피 — 감시 한 묶음을 데이터(yaml)로 둔다.

카테고리를 코드 모듈로 만들면 주제마다 코드를 짜야 한다. 대신 ``recipes/*.yaml`` 한 파일이
"무엇을 · 어떤 종류로 · 어디서 · 얼마나 · 어디로" 를 전부 들고 있고, 코드는 이 파일을 읽어
등록·갱신·삭제하는 한 벌만 있으면 된다. 새 주제는 LLM 이 파일 하나를 쓰는 일이 된다.

레시피가 정본이다. changedetection.io 의 데이터가 날아가도 ``sync`` 한 번으로 재생성된다.

파일 예시는 ``recipes/flight.yaml`` 참고. 필드 뜻:

    topic      ntfy 토픽 키. ``sys`` → ``NTFY_TOPIC``, 그 외 → ``NTFY_TOPIC_<KEY>``
    kind       diff | item | threshold | event   (신호의 모양. 판정 로직이 갈리는 단위)
    mode       instant | digest                   (발송 방식)
    runner     local | actions                    (어디서 폴링하나. fetch: browser 는 local 만)
    sources[]  type(feed|web|email|push|llm-search) · url · fetch · select · trigger · every
    verified   test_parse 통과 날짜. 없으면 등록을 거부한다 (검증 게이트)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

RECIPES_DIR = Path("recipes")

KINDS = ("diff", "item", "threshold", "event")
MODES = ("instant", "digest")
RUNNERS = ("local", "actions")
SOURCE_TYPES = ("feed", "web", "email", "push", "llm-search")
FETCHES = ("requests", "browser")

_INTERVAL = re.compile(r"^\s*(\d+)\s*([mhd])\s*$")


class RecipeError(ValueError):
    """레시피 파일이 형식에 맞지 않을 때."""


def parse_interval(text: str) -> dict:
    """``"30m"`` / ``"1h"`` / ``"2d"`` 를 changedetection.io 의 time_between_check 로."""
    m = _INTERVAL.match(str(text))
    if not m:
        raise RecipeError(f"간격 형식이 잘못됐습니다: {text!r} (예: 30m, 1h, 2d)")
    n, unit = int(m.group(1)), m.group(2)
    return {
        "weeks": 0,
        "days": n if unit == "d" else 0,
        "hours": n if unit == "h" else 0,
        "minutes": n if unit == "m" else 0,
        "seconds": 0,
    }


def interval_seconds(text: str) -> int:
    d = parse_interval(text)
    return d["days"] * 86400 + d["hours"] * 3600 + d["minutes"] * 60


def topic_env_name(topic: str) -> str:
    return "NTFY_TOPIC" if topic == "sys" else f"NTFY_TOPIC_{topic.upper()}"


def resolve_topic(topic: str) -> str:
    """토픽 키를 실제 ntfy 토픽 문자열로. 환경 변수가 없으면 즉시 실패한다."""
    name = topic_env_name(topic)
    value = os.environ.get(name)
    if not value:
        raise RecipeError(f"토픽 {topic!r} 에 해당하는 환경 변수 {name} 이 설정되지 않았습니다")
    return value


@dataclass
class Source:
    type: str
    url: str = ""
    title: str = ""
    fetch: str = "requests"
    select: list[str] = field(default_factory=list)
    trigger: list[str] = field(default_factory=list)
    every: str = "1h"

    def validate(self, where: str) -> None:
        if self.type not in SOURCE_TYPES:
            raise RecipeError(f"{where}: type 은 {SOURCE_TYPES} 중 하나여야 합니다: {self.type!r}")
        if self.fetch not in FETCHES:
            raise RecipeError(f"{where}: fetch 는 {FETCHES} 중 하나여야 합니다: {self.fetch!r}")
        if self.type in ("feed", "web") and not self.url:
            raise RecipeError(f"{where}: url 이 필요합니다")
        parse_interval(self.every)


@dataclass
class Recipe:
    name: str
    topic: str
    kind: str
    sources: list[Source]
    mode: str = "instant"
    runner: str = "local"
    digest_every: str = "6h"
    quiet: list[str] = field(default_factory=list)
    summarize: bool = False
    verified: date | None = None
    path: Path | None = None

    # ── 검증 ────────────────────────────────────────────────────────────

    def validate(self) -> None:
        w = f"레시피 {self.name}"
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", self.name):
            raise RecipeError(f"{w}: 이름은 소문자·숫자·하이픈만 씁니다")
        if self.kind not in KINDS:
            raise RecipeError(f"{w}: kind 는 {KINDS} 중 하나여야 합니다: {self.kind!r}")
        if self.mode not in MODES:
            raise RecipeError(f"{w}: mode 는 {MODES} 중 하나여야 합니다: {self.mode!r}")
        if self.runner not in RUNNERS:
            raise RecipeError(f"{w}: runner 는 {RUNNERS} 중 하나여야 합니다: {self.runner!r}")
        if not self.sources:
            raise RecipeError(f"{w}: sources 가 비어 있습니다")
        for i, s in enumerate(self.sources):
            s.validate(f"{w} sources[{i}]")
            if s.fetch == "browser" and self.runner != "local":
                raise RecipeError(
                    f"{w} sources[{i}]: fetch: browser 는 runner: local 에서만 됩니다"
                )
        if self.mode == "digest":
            parse_interval(self.digest_every)

    @property
    def is_verified(self) -> bool:
        return self.verified is not None

    # ── changedetection.io 로 내보내기 ──────────────────────────────────

    def watch_key(self, index: int) -> str:
        """changedetection.io 쪽에서 이 소스를 식별하는 제목 접두어."""
        return f"{self.name}/{index}"

    def to_watches(self) -> list[dict]:
        """changedetection.io 에 등록할 감시 목록. runner: local 인 web/feed 소스만."""
        from watchog import cdio  # 순환 import 회피

        if self.runner != "local":
            return []
        topic = resolve_topic(self.topic)
        out = []
        for i, s in enumerate(self.sources):
            if s.type not in ("feed", "web"):
                continue
            body: dict = {
                "url": s.url,
                "title": f"{self.watch_key(i)} · {s.title or s.url}",
                "tag": self.topic,
                "notification_urls": [cdio.ntfy_url(topic)],
                "time_between_check": parse_interval(s.every),
                "fetch_backend": cdio.FETCH_BROWSER if s.fetch == "browser" else cdio.FETCH_REQUESTS,
            }
            if s.select:
                body["include_filters"] = list(s.select)
            if s.trigger:
                body["trigger_text"] = list(s.trigger)
            out.append(body)
        return out


# ── 파일 입출력 ──────────────────────────────────────────────────────────


def _source_from(raw: dict, where: str) -> Source:
    if not isinstance(raw, dict):
        raise RecipeError(f"{where}: 소스는 매핑이어야 합니다")
    select = raw.get("select", [])
    if isinstance(select, str):
        select = [select]
    trigger = raw.get("trigger", [])
    if isinstance(trigger, str):
        trigger = [trigger]
    return Source(
        type=str(raw.get("type", "web")),
        url=str(raw.get("url", "")),
        title=str(raw.get("title", "")),
        fetch=str(raw.get("fetch", "requests")),
        select=[str(x) for x in select],
        trigger=[str(x) for x in trigger],
        every=str(raw.get("every", "1h")),
    )


def load(path: str | Path) -> Recipe:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise RecipeError(f"{path}: 최상위는 매핑이어야 합니다")
    name = str(raw.get("name") or path.stem)
    verified = raw.get("verified")
    if isinstance(verified, str):
        verified = date.fromisoformat(verified)
    quiet = raw.get("quiet", [])
    if isinstance(quiet, str):
        quiet = [quiet]
    r = Recipe(
        name=name,
        topic=str(raw.get("topic", "sys")),
        kind=str(raw.get("kind", "diff")),
        mode=str(raw.get("mode", "instant")),
        runner=str(raw.get("runner", "local")),
        digest_every=str(raw.get("digest_every", "6h")),
        quiet=[str(x) for x in quiet],
        summarize=bool(raw.get("summarize", False)),
        verified=verified,
        sources=[_source_from(s, f"{path} sources[{i}]") for i, s in enumerate(raw.get("sources") or [])],
        path=path,
    )
    r.validate()
    return r


def load_all(directory: str | Path = RECIPES_DIR) -> list[Recipe]:
    directory = Path(directory)
    if not directory.exists():
        return []
    return [load(p) for p in sorted(directory.glob("*.yaml"))]


def dump(recipe: Recipe) -> str:
    """레시피를 yaml 문자열로. LLM 초안 저장과 테스트에 쓴다."""
    doc: dict = {
        "name": recipe.name,
        "topic": recipe.topic,
        "kind": recipe.kind,
        "mode": recipe.mode,
        "runner": recipe.runner,
    }
    if recipe.mode == "digest":
        doc["digest_every"] = recipe.digest_every
    if recipe.quiet:
        doc["quiet"] = recipe.quiet
    if recipe.summarize:
        doc["summarize"] = True
    doc["sources"] = []
    for s in recipe.sources:
        d: dict = {"type": s.type}
        if s.url:
            d["url"] = s.url
        if s.title:
            d["title"] = s.title
        if s.fetch != "requests":
            d["fetch"] = s.fetch
        if s.select:
            d["select"] = s.select
        if s.trigger:
            d["trigger"] = s.trigger
        d["every"] = s.every
        doc["sources"].append(d)
    doc["verified"] = recipe.verified.isoformat() if recipe.verified else None
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


def save(recipe: Recipe, directory: str | Path = RECIPES_DIR) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{recipe.name}.yaml"
    path.write_text(dump(recipe), encoding="utf-8")
    recipe.path = path
    return path


# ── changedetection.io 동기화 ────────────────────────────────────────────


def sync(recipes: list[Recipe], client, *, dry_run: bool = False) -> dict:
    """레시피 → changedetection.io. 추가·갱신·삭제 결과를 돌려준다.

    기존 감시는 (1) 제목 접두어 ``name/i`` 로, 없으면 (2) URL 로 찾아 **입양**한다.
    접두어가 붙어 있는데 어느 레시피에도 없는 감시는 지운다. 접두어도 URL 도 안 맞는
    감시(손으로 만든 것)는 건드리지 않는다.
    """
    wanted: dict[str, dict] = {}
    for r in recipes:
        if not r.is_verified:
            raise RecipeError(f"레시피 {r.name}: verified 가 없어 등록할 수 없습니다 (test_parse 먼저)")
        for i, body in enumerate(r.to_watches()):
            wanted[r.watch_key(i)] = body

    existing = client.list_watches()
    by_key: dict[str, str] = {}
    by_url: dict[str, str] = {}
    for uuid, w in existing.items():
        title = w.get("title") or ""
        m = re.match(r"^([a-z0-9][a-z0-9-]*/\d+) · ", title)
        if m:
            by_key[m.group(1)] = uuid
        elif w.get("url"):
            by_url.setdefault(w["url"], uuid)

    result = {"added": [], "updated": [], "removed": [], "unchanged": []}
    for key, body in wanted.items():
        uuid = by_key.get(key) or by_url.get(body["url"])
        if uuid:
            action = "updated"
            if not dry_run:
                client.update_watch(uuid, **body)
        else:
            action = "added"
            if not dry_run:
                client.add_watch(**body)
        result[action].append(key)
    for key, uuid in by_key.items():
        if key not in wanted:
            if not dry_run:
                client.remove_watch(uuid)
            result["removed"].append(key)
    return result
