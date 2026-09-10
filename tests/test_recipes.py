"""레시피 로더·검증·동기화 테스트. 네트워크를 쓰지 않는다."""

from datetime import date
from pathlib import Path

import pytest

from watchog import recipes

FLIGHT_YAML = """
name: flight
topic: flight
kind: item
mode: instant
runner: local
sources:
  - type: web
    title: Google Flights
    url: https://example.com/deals
    fetch: browser
    every: 12h
  - type: feed
    url: https://example.com/rss
    select: xpath://item/title
    trigger: [항공]
    every: 1h
verified: 2026-09-11
"""


def write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / f"{name}.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_parse_interval():
    assert recipes.parse_interval("30m")["minutes"] == 30
    assert recipes.parse_interval("12h")["hours"] == 12
    assert recipes.parse_interval("2d")["days"] == 2
    assert recipes.interval_seconds("1h") == 3600
    with pytest.raises(recipes.RecipeError):
        recipes.parse_interval("soon")


def test_load_recipe(tmp_path):
    r = recipes.load(write(tmp_path, "flight", FLIGHT_YAML))
    assert r.name == "flight"
    assert r.kind == "item"
    assert r.verified == date(2026, 9, 11)
    assert [s.type for s in r.sources] == ["web", "feed"]
    assert r.sources[1].select == ["xpath://item/title"]
    assert r.sources[1].trigger == ["항공"]


def test_browser_requires_local_runner(tmp_path):
    bad = FLIGHT_YAML.replace("runner: local", "runner: actions")
    with pytest.raises(recipes.RecipeError, match="browser"):
        recipes.load(write(tmp_path, "flight", bad))


def test_bad_kind_rejected(tmp_path):
    bad = FLIGHT_YAML.replace("kind: item", "kind: magic")
    with pytest.raises(recipes.RecipeError, match="kind"):
        recipes.load(write(tmp_path, "flight", bad))


def test_topic_env(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "sys-x")
    monkeypatch.setenv("NTFY_TOPIC_FLIGHT", "flight-x")
    assert recipes.resolve_topic("sys") == "sys-x"
    assert recipes.resolve_topic("flight") == "flight-x"
    monkeypatch.delenv("NTFY_TOPIC_JOBS", raising=False)
    with pytest.raises(recipes.RecipeError, match="NTFY_TOPIC_JOBS"):
        recipes.resolve_topic("jobs")


def test_to_watches(tmp_path, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC_FLIGHT", "flight-x")
    r = recipes.load(write(tmp_path, "flight", FLIGHT_YAML))
    watches = r.to_watches()
    assert [w["title"].split(" · ")[0] for w in watches] == ["flight/0", "flight/1"]
    assert watches[0]["fetch_backend"] == "html_webdriver"
    assert watches[1]["include_filters"] == ["xpath://item/title"]
    assert watches[1]["trigger_text"] == ["항공"]
    assert watches[1]["notification_urls"] == ["ntfys://ntfy.sh/flight-x"]
    assert watches[0]["time_between_check"]["hours"] == 12
    assert watches[0]["time_between_check_use_default"] is False


def test_actions_runner_exports_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC_FLIGHT", "flight-x")
    text = FLIGHT_YAML.replace("runner: local", "runner: actions").replace("    fetch: browser\n", "")
    r = recipes.load(write(tmp_path, "flight", text))
    assert r.to_watches() == []


def test_dump_roundtrip(tmp_path):
    r = recipes.load(write(tmp_path, "flight", FLIGHT_YAML))
    again = recipes.load(write(tmp_path, "flight2", recipes.dump(r).replace("name: flight", "name: flight2")))
    assert again.sources[0].fetch == "browser"
    assert again.sources[1].trigger == ["항공"]
    assert again.verified == r.verified


class FakeClient:
    """changedetection.io 흉내. 제목·URL 만 기억한다."""

    def __init__(self, existing: dict[str, dict]):
        self.watches = dict(existing)
        self.calls: list[tuple] = []

    def list_watches(self):
        return self.watches

    def add_watch(self, **body):
        uuid = f"new-{len(self.watches)}"
        self.watches[uuid] = {"title": body["title"], "url": body["url"]}
        self.calls.append(("add", body["title"]))
        return uuid

    def update_watch(self, uuid, **fields):
        self.watches[uuid].update({"title": fields["title"], "url": fields["url"]})
        self.calls.append(("update", uuid))

    def remove_watch(self, uuid):
        del self.watches[uuid]
        self.calls.append(("remove", uuid))


def test_sync_adopts_by_url_and_removes_orphans(tmp_path, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC_FLIGHT", "flight-x")
    r = recipes.load(write(tmp_path, "flight", FLIGHT_YAML))
    client = FakeClient({
        "a": {"title": "[flight] 손으로 만든 것", "url": "https://example.com/deals"},   # URL 로 입양
        "b": {"title": "old/0 · 옛 레시피", "url": "https://old.example.com"},           # 고아 → 삭제
        "c": {"title": "수동 감시", "url": "https://manual.example.com"},               # 건드리지 않음
    })
    result = recipes.sync([r], client)
    assert result["updated"] == ["flight/0"]
    assert result["added"] == ["flight/1"]
    assert result["removed"] == ["old/0"]
    assert "c" in client.watches and "b" not in client.watches
    assert client.watches["a"]["title"].startswith("flight/0 · ")


def test_sync_refuses_unverified(tmp_path, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC_FLIGHT", "flight-x")
    r = recipes.load(write(tmp_path, "flight", FLIGHT_YAML.replace("verified: 2026-09-11", "")))
    with pytest.raises(recipes.RecipeError, match="verified"):
        recipes.sync([r], FakeClient({}))


def test_dry_run_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC_FLIGHT", "flight-x")
    r = recipes.load(write(tmp_path, "flight", FLIGHT_YAML))
    client = FakeClient({})
    result = recipes.sync([r], client, dry_run=True)
    assert result["added"] == ["flight/0", "flight/1"]
    assert client.calls == []
