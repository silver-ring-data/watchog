"""알림 이력과 피드백 — sqlite 한 파일.

"지난주 리버풀 소식 뭐 있었지" 에 답하고, 임계값 튜닝·다이제스트 묶기·피드백 매칭이 전부 이 위에서 돈다.
피드백은 폰의 ntfy 알림에 붙은 「유용 / 무시」 버튼이 ntfy ``feedback`` 토픽으로 POST 한 것을
``pull_feedback`` 이 끌어와 이력에 붙인다. 서버를 열 필요가 없다 — ntfy 가 우편함이다.

테이블
    alerts(id, ts, recipe, source_index, title, body, url, topic, priority, item_ids)
    feedback(id, ts, alert_id, verdict, raw)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import requests

log = logging.getLogger(__name__)

DEFAULT_PATH = Path("data/history.sqlite")
NTFY_SERVER = "https://ntfy.sh"

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    recipe TEXT NOT NULL,
    source_index INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    url TEXT,
    topic TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'default',
    item_ids TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS alerts_recipe_ts ON alerts(recipe, ts);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    alert_id INTEGER,
    verdict TEXT NOT NULL,
    raw TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS cursors (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class AlertRow:
    id: int
    ts: int
    recipe: str
    title: str
    body: str
    url: str | None
    topic: str


class History:
    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> History:
        return self

    def __exit__(self, *exc) -> None:
        self.conn.commit()
        self.close()

    # ── 기록 ────────────────────────────────────────────────────────────

    def record(self, *, recipe: str, source_index: int, title: str, body: str, url: str | None,
               topic: str, priority: str = "default", item_ids: list[str] | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO alerts(ts, recipe, source_index, title, body, url, topic, priority, item_ids)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (int(time.time()), recipe, source_index, title, body, url, topic, priority,
             json.dumps(item_ids or [], ensure_ascii=False)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def recent(self, recipe: str | None = None, *, days: int = 7, limit: int = 50) -> list[AlertRow]:
        since = int(time.time()) - days * 86400
        if recipe:
            rows = self.conn.execute(
                "SELECT id, ts, recipe, title, body, url, topic FROM alerts WHERE recipe=? AND ts>=? ORDER BY ts DESC LIMIT ?",
                (recipe, since, limit))
        else:
            rows = self.conn.execute(
                "SELECT id, ts, recipe, title, body, url, topic FROM alerts WHERE ts>=? ORDER BY ts DESC LIMIT ?",
                (since, limit))
        return [AlertRow(*r) for r in rows]

    def add_feedback(self, alert_id: int | None, verdict: str, raw: str = "") -> None:
        self.conn.execute("INSERT INTO feedback(ts, alert_id, verdict, raw) VALUES (?,?,?,?)",
                          (int(time.time()), alert_id, verdict, raw))
        self.conn.commit()

    def feedback_summary(self, recipe: str | None = None) -> dict[str, int]:
        q = ("SELECT f.verdict, COUNT(*) FROM feedback f LEFT JOIN alerts a ON a.id=f.alert_id"
             + (" WHERE a.recipe=?" if recipe else "") + " GROUP BY f.verdict")
        rows = self.conn.execute(q, (recipe,) if recipe else ()).fetchall()
        return {v: n for v, n in rows}

    # ── 커서 ────────────────────────────────────────────────────────────

    def get_cursor(self, name: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM cursors WHERE name=?", (name,)).fetchone()
        return row[0] if row else default

    def set_cursor(self, name: str, value: str) -> None:
        self.conn.execute("INSERT INTO cursors(name, value) VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                          (name, value))
        self.conn.commit()


# ── 피드백 버튼 ───────────────────────────────────────────────────────────


def feedback_actions(alert_id: int, topic: str | None = None, server: str = NTFY_SERVER) -> list[dict]:
    """ntfy 알림에 붙일 「유용 / 무시」 버튼. 누르면 feedback 토픽으로 한 줄이 POST 된다."""
    topic = topic or os.environ.get("NTFY_TOPIC_FEEDBACK")
    if not topic:
        return []
    def action(label: str, verdict: str) -> dict:
        return {
            "action": "http",
            "label": label,
            "url": f"{server}/{topic}",
            "method": "POST",
            "body": json.dumps({"alert_id": alert_id, "verdict": verdict}),
            "clear": True,
        }
    return [action("👍 유용", "useful"), action("🙈 무시", "ignore")]


def pull_feedback(history: History, topic: str | None = None, server: str = NTFY_SERVER) -> int:
    """feedback 토픽에 쌓인 것을 끌어와 이력에 붙인다. 마지막 메시지 id 를 커서로 기억한다."""
    topic = topic or os.environ.get("NTFY_TOPIC_FEEDBACK")
    if not topic:
        return 0
    since = history.get_cursor("feedback", "all")
    try:
        resp = requests.get(f"{server}/{topic}/json", params={"poll": "1", "since": since}, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("피드백 수신 실패: %s", e)
        return 0
    n = 0
    last_id = None
    for line in resp.text.splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("event") != "message":
            continue
        last_id = msg.get("id") or last_id
        raw = msg.get("message", "")
        try:
            payload = json.loads(raw)
            history.add_feedback(payload.get("alert_id"), str(payload.get("verdict", "unknown")), raw)
        except ValueError:
            history.add_feedback(None, "unknown", raw)
        n += 1
    if last_id:
        history.set_cursor("feedback", last_id)
    return n
