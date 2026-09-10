"""Thin client for the changedetection.io REST API.

watchog does not crawl anything itself. changedetection.io polls, renders and
diffs; this module is the only place that knows its API shape, so the MCP
server and the CLI both go through it.

API reference: https://changedetection.io/docs/api_v1/index.html
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests

DEFAULT_URL = "http://localhost:5000"

# Fetch backends understood by changedetection.io.
FETCH_REQUESTS = "html_requests"     # plain HTTP, cheap
FETCH_BROWSER = "html_webdriver"     # Playwright via the browser container


def interval(*, minutes: int = 0, hours: int = 0, days: int = 0) -> dict:
    """Build the ``time_between_check`` structure the API expects."""
    return {"weeks": 0, "days": days, "hours": hours, "minutes": minutes, "seconds": 0}


def ntfy_url(topic: str, server: str = "ntfy.sh") -> str:
    """Apprise-style ntfy URL that changedetection.io sends notifications to."""
    return f"ntfys://{server}/{topic}"


@dataclass
class ChangeDetection:
    base_url: str
    api_key: str

    @classmethod
    def from_env(cls) -> "ChangeDetection":
        key = os.environ.get("CDIO_API_KEY")
        if not key:
            raise RuntimeError(
                "CDIO_API_KEY is not set -- copy it from the changedetection.io "
                "UI (Settings -> API) into .env"
            )
        return cls(os.environ.get("CDIO_URL", DEFAULT_URL).rstrip("/"), key)

    # -- internals -----------------------------------------------------------

    def _request(self, method: str, path: str, **kw: Any) -> Any:
        resp = requests.request(
            method,
            f"{self.base_url}/api/v1{path}",
            headers={"x-api-key": self.api_key},
            timeout=30,
            **kw,
        )
        resp.raise_for_status()
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # -- watches -------------------------------------------------------------

    def system_info(self) -> dict:
        return self._request("GET", "/systeminfo")

    def list_watches(self) -> dict[str, dict]:
        """Return ``{uuid: summary}`` for every watch."""
        return self._request("GET", "/watch")

    def get_watch(self, uuid: str) -> dict:
        return self._request("GET", f"/watch/{uuid}")

    def add_watch(
        self,
        url: str,
        *,
        title: str | None = None,
        tag: str | None = None,
        notification_urls: list[str] | None = None,
        time_between_check: dict | None = None,
        fetch_backend: str | None = None,
        include_filters: list[str] | None = None,
        trigger_text: list[str] | None = None,
    ) -> str:
        """Create a watch and return its uuid.

        ``tag`` is the module name (movie / jobs / flight ...). ``include_filters``
        are CSS or XPath selectors that narrow the compared region -- the
        difference between "this page changed" and "this list changed".
        """
        body: dict[str, Any] = {"url": url}
        if title:
            body["title"] = title
        if tag:
            body["tag"] = tag
        if notification_urls:
            body["notification_urls"] = notification_urls
        if time_between_check:
            body["time_between_check"] = time_between_check
        if fetch_backend:
            body["fetch_backend"] = fetch_backend
        if include_filters:
            body["include_filters"] = include_filters
        if trigger_text:
            body["trigger_text"] = trigger_text
        created = self._request("POST", "/watch", json=body)
        return created["uuid"]

    def update_watch(self, uuid: str, **fields: Any) -> None:
        self._request("PUT", f"/watch/{uuid}", json=fields)

    def remove_watch(self, uuid: str) -> None:
        self._request("DELETE", f"/watch/{uuid}")

    def recheck(self, uuid: str) -> None:
        self._request("GET", f"/watch/{uuid}", params={"recheck": "1"})

    def history(self, uuid: str) -> dict[str, str]:
        """Return ``{timestamp: snapshot_path}`` for a watch."""
        return self._request("GET", f"/watch/{uuid}/history")
