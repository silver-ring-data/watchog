"""Alert model and dispatch.

Watchers never talk to a notification service directly -- they build an
``Alert`` and hand it to :func:`dispatch`. Swapping ntfy for Telegram, SMS or
e-mail later means adding a module under ``watchog/sinks/`` and naming it in
the config; no watcher changes.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Ordered from quietest to loudest. "urgent" is what breaks through a phone's
# silent / do-not-disturb mode -- reserve it for things that expire, like a
# ticket window opening.
PRIORITIES = ("min", "low", "default", "high", "urgent")


@dataclass
class Alert:
    """One thing worth interrupting the user for."""

    title: str
    body: str
    url: str | None = None          # tapping the notification opens this
    priority: str = "default"
    tags: list[str] = field(default_factory=list)   # emoji shortcodes
    source: str = ""                # watcher name, for logging

    def __post_init__(self) -> None:
        if self.priority not in PRIORITIES:
            raise ValueError(
                f"priority must be one of {PRIORITIES}, got {self.priority!r}"
            )


def _load_sink(name: str):
    return importlib.import_module(f"watchog.sinks.{name}")


def dispatch(alert: Alert, sinks: dict[str, dict]) -> int:
    """Send ``alert`` to every configured sink. Returns the number delivered.

    A sink that raises is logged and skipped -- one broken channel must not
    stop the others, since the whole point is not missing the alert.
    """
    delivered = 0
    for name, cfg in sinks.items():
        if cfg.get("enabled") is False:
            continue
        try:
            _load_sink(name).send(alert, cfg)
        except Exception:
            log.exception("sink %r failed for alert %r", name, alert.title)
        else:
            delivered += 1
            log.info("sent via %s: %s", name, alert.title)

    if not delivered:
        log.error("no sink delivered alert: %s", alert.title)
    return delivered
