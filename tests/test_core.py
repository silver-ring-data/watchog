"""seen-set 과 ntfy sink 테스트."""

import json

import pytest

from watchog.core.notify import Alert, dispatch
from watchog.core.state import SeenStore
from watchog.sinks import ntfy


def test_seen_store_roundtrip(tmp_path):
    path = tmp_path / "seen.json"
    with SeenStore(path) as s:
        assert s.is_new("jobs", "a")
        s.mark("jobs", "a")
        assert not s.is_new("jobs", "a")
        assert s.filter_new("jobs", ["a", "b"], lambda x: x) == ["b"]
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "jobs:a" in data["seen"]
    # 다시 열어도 기억한다
    assert not SeenStore(path).is_new("jobs", "a")


def test_seen_store_survives_corrupt_file(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text("{not json", encoding="utf-8")
    assert len(SeenStore(path)) == 0


def test_seen_store_prunes_old(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text(json.dumps({"seen": {"x:old": "2000-01-01T00:00:00+00:00"}}), encoding="utf-8")
    s = SeenStore(path, retention_days=1)
    assert s.prune() == 1
    assert s.is_new("x", "old")


def test_alert_rejects_bad_priority():
    with pytest.raises(ValueError):
        Alert(title="t", body="b", priority="loud")


def test_ntfy_payload(monkeypatch):
    sent = {}

    class Resp:
        def raise_for_status(self):
            pass

    def fake_post(url, json=None, headers=None, timeout=None):
        sent["url"], sent["json"], sent["headers"] = url, json, headers
        return Resp()

    monkeypatch.setattr(ntfy.requests, "post", fake_post)
    monkeypatch.delenv("NTFY_TOKEN", raising=False)
    alert = Alert(title="예매 오픈", body="용산 IMAX", url="https://example.com", priority="urgent", tags=["eyes"])
    ntfy.send(alert, {"topic": "watchog-x"})
    assert sent["url"] == "https://ntfy.sh"
    assert sent["json"]["topic"] == "watchog-x"
    assert sent["json"]["title"] == "예매 오픈"
    assert sent["json"]["priority"] == 5
    assert sent["json"]["click"] == "https://example.com"
    assert sent["json"]["tags"] == ["eyes"]
    assert "Authorization" not in sent["headers"]


def test_ntfy_requires_topic(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    with pytest.raises(RuntimeError, match="topic"):
        ntfy.send(Alert(title="t", body="b"), {})


def test_dispatch_counts_and_survives_failure(monkeypatch):
    import importlib

    class Good:
        @staticmethod
        def send(alert, cfg):
            pass

    class Bad:
        @staticmethod
        def send(alert, cfg):
            raise RuntimeError("boom")

    monkeypatch.setattr(importlib, "import_module",
                        lambda name: Good if name.endswith(".good") else Bad)
    n = dispatch(Alert(title="t", body="b"), {"good": {}, "bad": {}, "off": {"enabled": False}})
    assert n == 1
