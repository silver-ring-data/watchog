"""Remembering what has already been seen.

This is the single most important piece of the whole project. A watcher polling
every five minutes re-discovers the same items on every run; without a seen-set
it would re-notify forever, and for LLM-scored watchers it would re-spend tokens
on items it already judged. Deduplication is what keeps both under control.

The store is a plain JSON file so it can be committed back by the GitHub
Actions run -- there is no database to host.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)


class SeenStore:
    """A namespaced set of keys with timestamps, persisted to JSON."""

    def __init__(self, path: str | Path, retention_days: int = 90) -> None:
        self.path = Path(path)
        self.retention_days = retention_days
        self._seen: dict[str, str] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._seen = raw.get("seen", {})
        except (OSError, ValueError):
            # A corrupt state file must not wedge the watcher. Worst case we
            # re-notify once, which is far better than crashing every run.
            log.warning("could not read state at %s, starting empty", self.path)
            self._seen = {}

    @staticmethod
    def _key(namespace: str, item_id: str) -> str:
        return f"{namespace}:{item_id}"

    def is_new(self, namespace: str, item_id: str) -> bool:
        return self._key(namespace, item_id) not in self._seen

    def mark(self, namespace: str, item_id: str) -> None:
        self._seen[self._key(namespace, item_id)] = datetime.now(
            UTC
        ).isoformat(timespec="seconds")
        self._dirty = True

    def filter_new(self, namespace: str, items, id_of) -> list:
        """Return only the items not seen before. Does not mark them.

        Marking is deliberately separate so a crash between filtering and
        notifying does not silently swallow an alert.
        """
        return [it for it in items if self.is_new(namespace, id_of(it))]

    def prune(self) -> int:
        """Drop entries older than the retention window. Returns count removed."""
        cutoff = datetime.now(UTC) - timedelta(days=self.retention_days)
        stale = []
        for key, stamp in self._seen.items():
            try:
                if datetime.fromisoformat(stamp) < cutoff:
                    stale.append(key)
            except ValueError:
                stale.append(key)
        for key in stale:
            del self._seen[key]
        if stale:
            self._dirty = True
        return len(stale)

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"seen": dict(sorted(self._seen.items()))}
        # Write via a temp file so an interrupted run cannot truncate the
        # existing state into an empty file.
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        tmp.replace(self.path)
        self._dirty = False

    def __enter__(self) -> SeenStore:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.prune()
            self.save()

    def __len__(self) -> int:
        return len(self._seen)
