"""러너 · 운영 점검 · 이력 테스트. 네트워크는 전부 monkeypatch."""

import json

from watchog import extract, history, ops, runner
from watchog.core.state import SeenStore
from watchog.recipes import Recipe, Source

HTML1 = '<div class="l"><a href="/1">첫 공고입니다</a><a href="/2">둘째 공고입니다</a></div>'
HTML2 = '<div class="l"><a href="/1">첫 공고입니다</a><a href="/2">둘째 공고입니다</a><a href="/3">셋째 항공 특가</a></div>'


def recipe(trigger=None, runner_name="actions"):
    return Recipe(name="t", topic="flight", kind="item", runner=runner_name, verified=__import__("datetime").date(2026, 9, 11),
                  sources=[Source(type="web", url="https://example.com/l", title="예시", select=["div.l a"],
                                  trigger=trigger or [])])


def fake_fetch(text):
    return lambda url, check_robots=True: extract.Fetched(url=url, status=200, content_type="text/html", text=text)


def test_item_id_normalizes():
    assert runner.item_id("[효성ITX] AI MLOps 엔지니어") == runner.item_id("효성itx ai mlops 엔지니어 ")


def test_run_source_reports_only_new(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "fetch", fake_fetch(HTML1))
    r = recipe()
    with SeenStore(tmp_path / "seen.json") as seen:
        first = runner.run_source(r, 0, r.sources[0], seen)
        assert first.ok and len(first.new_items) == 2
        second = runner.run_source(r, 0, r.sources[0], seen)
        assert second.new_items == []
    monkeypatch.setattr(extract, "fetch", fake_fetch(HTML2))
    with SeenStore(tmp_path / "seen.json") as seen:
        third = runner.run_source(r, 0, r.sources[0], seen)
        assert third.new_items == ["셋째 항공 특가"]


def test_trigger_filters_items(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "fetch", fake_fetch(HTML2))
    r = recipe(trigger=["항공"])
    with SeenStore(tmp_path / "seen.json") as seen:
        res = runner.run_source(r, 0, r.sources[0], seen)
    assert res.new_items == ["셋째 항공 특가"]


def test_run_source_categorizes_failures(tmp_path, monkeypatch):
    def boom(url, check_robots=True):
        raise extract.ExtractError("blocked", "403")
    monkeypatch.setattr(extract, "fetch", boom)
    r = recipe()
    with SeenStore(tmp_path / "seen.json") as seen:
        res = runner.run_source(r, 0, r.sources[0], seen)
    assert not res.ok and res.category == "blocked"


def test_run_dry_run_sends_nothing_and_keeps_no_state(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "fetch", fake_fetch(HTML1))
    sent = []
    monkeypatch.setattr(runner, "dispatch", lambda alert, sinks: sent.append(alert))
    monkeypatch.setenv("NTFY_TOPIC_FLIGHT", "flight-x")
    path = tmp_path / "seen.json"
    results = runner.run([recipe()], state_path=str(path), dry_run=True)
    assert len(results) == 1 and len(results[0].new_items) == 2
    assert sent == []
    assert not path.exists()


def test_run_sends_alert_with_feedback_buttons(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "fetch", fake_fetch(HTML1))
    monkeypatch.setattr(history, "DEFAULT_PATH", tmp_path / "h.sqlite")
    monkeypatch.setattr(history.History.__init__, "__defaults__", (tmp_path / "h.sqlite",))
    sent = []
    monkeypatch.setattr(runner, "dispatch", lambda alert, sinks: sent.append((alert, sinks)))
    monkeypatch.setenv("NTFY_TOPIC_FLIGHT", "flight-x")
    monkeypatch.setenv("NTFY_TOPIC_FEEDBACK", "fb-x")
    runner.run([recipe()], state_path=str(tmp_path / "seen.json"))
    assert len(sent) == 1
    alert, sinks = sent[0]
    assert sinks == {"ntfy": {"topic": "flight-x"}}
    assert "새 항목 2건" in alert.title
    assert [a["label"] for a in alert.actions] == ["👍 유용", "🙈 무시"]
    assert json.loads(alert.actions[0]["body"])["verdict"] == "useful"
    with history.History(tmp_path / "h.sqlite") as h:
        rows = h.recent("t")
    assert len(rows) == 1 and rows[0].topic == "flight"


def test_run_skips_other_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "fetch", fake_fetch(HTML1))
    results = runner.run([recipe(runner_name="local")], state_path=str(tmp_path / "seen.json"), only_runner="actions")
    assert results == []


def test_history_feedback_roundtrip(tmp_path):
    with history.History(tmp_path / "h.sqlite") as h:
        aid = h.record(recipe="t", source_index=0, title="x", body="", url=None, topic="flight")
        h.add_feedback(aid, "useful")
        h.add_feedback(aid, "ignore")
        assert h.feedback_summary("t") == {"useful": 1, "ignore": 1}
        h.set_cursor("feedback", "abc")
        assert h.get_cursor("feedback") == "abc"


def test_pull_feedback_parses_ntfy_json(tmp_path, monkeypatch):
    lines = "\n".join([
        json.dumps({"event": "open"}),
        json.dumps({"id": "m1", "event": "message", "message": json.dumps({"alert_id": 1, "verdict": "useful"})}),
        json.dumps({"id": "m2", "event": "message", "message": "not json"}),
    ])

    class Resp:
        text = lines
        def raise_for_status(self): pass

    monkeypatch.setattr(history.requests, "get", lambda url, params=None, timeout=None: Resp())
    with history.History(tmp_path / "h.sqlite") as h:
        n = history.pull_feedback(h, topic="fb-x")
        assert n == 2
        assert h.feedback_summary() == {"useful": 1, "unknown": 1}
        assert h.get_cursor("feedback") == "m2"


def test_ops_daily_reports_problems(monkeypatch):
    monkeypatch.delenv("CDIO_API_KEY", raising=False)
    monkeypatch.setattr(ops, "recheck_recipes", lambda rs: ["[empty] t/0 예시: 셀렉터가 0건"])
    sent = []
    monkeypatch.setattr(ops, "dispatch", lambda alert, sinks: sent.append(alert))
    monkeypatch.setenv("NTFY_TOPIC", "sys-x")
    report = ops.daily([recipe()])
    assert report.engine == "not-configured"
    assert not report.ok
    assert len(sent) == 1 and "문제 1건" in sent[0].title


def test_ops_daily_quiet_when_fine(monkeypatch):
    monkeypatch.delenv("CDIO_API_KEY", raising=False)
    monkeypatch.delenv("HEALTHCHECK_URL", raising=False)
    monkeypatch.setattr(ops, "recheck_recipes", lambda rs: [])
    sent = []
    monkeypatch.setattr(ops, "dispatch", lambda alert, sinks: sent.append(alert))
    report = ops.daily([recipe()])
    assert report.ok and sent == [] and report.heartbeat == "skipped"


def test_heartbeat_fail_suffix(monkeypatch):
    calls = []

    class Resp:
        def raise_for_status(self): pass

    monkeypatch.setattr(ops.requests, "get", lambda url, timeout=None: calls.append(url) or Resp())
    assert ops.ping_heartbeat("https://hc-ping.com/abc", fail=True) == "sent"
    assert calls == ["https://hc-ping.com/abc/fail"]
