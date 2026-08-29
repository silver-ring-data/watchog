"""ntfy.sh sink.

Publishes with ntfy's JSON format rather than the header-based one. ntfy's
header API expects latin-1, so Korean titles have to be RFC 2047 encoded to
survive; the JSON body is UTF-8 throughout and sidesteps that entirely.
"""

from __future__ import annotations

import os

import requests

from watchog.core.notify import Alert

DEFAULT_SERVER = "https://ntfy.sh"

# ntfy takes 1-5 in the JSON format; our names map onto that scale.
_PRIORITY_NUM = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5}


def send(alert: Alert, cfg: dict) -> None:
    topic = cfg.get("topic") or os.environ.get("NTFY_TOPIC")
    if not topic:
        raise RuntimeError(
            "ntfy topic missing -- set sinks.ntfy.topic in the config "
            "or the NTFY_TOPIC environment variable"
        )

    server = (cfg.get("server") or DEFAULT_SERVER).rstrip("/")

    payload: dict = {
        "topic": topic,
        "title": alert.title,
        "message": alert.body,
        "priority": _PRIORITY_NUM[alert.priority],
    }
    if alert.tags:
        payload["tags"] = alert.tags
    if alert.url:
        # Opens straight from the notification -- worth seconds when a
        # booking window has just opened.
        payload["click"] = alert.url

    headers = {}
    # Reserved topics on a self-hosted or paid server need auth; the free
    # public server does not.
    token = os.environ.get("NTFY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    resp = requests.post(server, json=payload, headers=headers, timeout=15)
    resp.raise_for_status()
