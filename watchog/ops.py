"""운영 — 하루 한 번 도는 것들. 하트비트 · 레시피 재검사 · 엔진 상태.

"멈춘 걸 모르는 상태" 를 막는 층이다 (설계 원칙 3·4).

- 하트비트: healthchecks.io 같은 dead-man's switch 에 핑을 보낸다. 핑이 끊기면 그쪽이 ntfy 로 알린다.
  PC 가 꺼져 있으면 우리 코드는 아무것도 못 보내므로, "안 오면 알리는" 바깥 서비스가 필요하다.
- 재검사: 모든 레시피의 requests 소스에 test_parse 를 다시 돌려 0건·구조 변화를 ``changed`` 범주로 sys 토픽에.
  등록 때만 검증하면 셀렉터가 깨진 뒤 조용히 0건이 된다.
- 엔진 상태: changedetection.io 가 켜져 있고 밀린 감시가 없는지.

실행:  python -m watchog ops daily        (작업 스케줄러에 하루 1회)
환경:  HEALTHCHECK_URL   healthchecks.io 의 핑 URL (없으면 하트비트는 건너뛴다)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import requests

from watchog import extract
from watchog.core.notify import Alert, dispatch
from watchog.recipes import Recipe, resolve_topic

log = logging.getLogger(__name__)


@dataclass
class DailyReport:
    heartbeat: str = "skipped"
    engine: str = "unknown"
    checked: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems and self.engine in ("ok", "not-configured")


def ping_heartbeat(url: str | None = None, *, fail: bool = False) -> str:
    url = url or os.environ.get("HEALTHCHECK_URL")
    if not url:
        return "skipped"
    target = url.rstrip("/") + ("/fail" if fail else "")
    try:
        resp = requests.get(target, timeout=15)
        resp.raise_for_status()
        return "sent"
    except requests.RequestException as e:
        log.warning("하트비트 실패: %s", e)
        return f"error: {e}"


def check_engine() -> tuple[str, list[str]]:
    """changedetection.io 가 살아 있고 밀린 감시가 없는지."""
    from watchog.cdio import ChangeDetection

    if not os.environ.get("CDIO_API_KEY"):
        return "not-configured", []
    try:
        info = ChangeDetection.from_env().system_info()
    except Exception as e:
        return "down", [f"changedetection.io 응답 없음: {e}"]
    problems = []
    overdue = info.get("overdue_watches") or []
    total = int(info.get("watch_count") or 0)
    # 큐가 비어 있는데 절반 넘게 밀려 있으면 워커가 멈춘 것이다. 큐가 차 있으면 그냥 바쁜 것.
    if total and len(overdue) > max(3, total // 2) and int(info.get("queue_size") or 0) == 0:
        problems.append(f"changedetection.io 워커가 멈춘 듯합니다 — 밀린 감시 {len(overdue)}/{total}건, 큐 비어 있음")
    return "ok", problems


def recheck_recipes(recipes_list: list[Recipe]) -> list[str]:
    """requests 소스마다 test_parse 를 다시 돌린다. 문제만 돌려준다."""
    problems: list[str] = []
    for r in recipes_list:
        for i, s in enumerate(r.sources):
            if s.type not in ("feed", "web") or s.fetch == "browser":
                continue
            selector = s.select[0] if s.select else None
            try:
                res = extract.test_parse(s.url, selector)
            except extract.ExtractError as e:
                problems.append(f"[{e.category}] {r.name}/{i} {s.title or s.url}: {e}")
                continue
            if selector and len(res.items) == 0:
                problems.append(f"[empty] {r.name}/{i} {s.title or s.url}: 셀렉터가 0건")
    return problems


def daily(recipes_list: list[Recipe], *, dry_run: bool = False) -> DailyReport:
    report = DailyReport()
    report.engine, engine_problems = check_engine()
    report.problems += engine_problems
    report.problems += recheck_recipes(recipes_list)
    report.checked = sum(1 for r in recipes_list for s in r.sources if s.fetch != "browser")
    report.heartbeat = "skipped" if dry_run else ping_heartbeat(fail=not report.ok)

    if report.problems:
        alert = Alert(
            title=f"watchog 일일 점검 — 문제 {len(report.problems)}건",
            body="\n".join(p[:120] for p in report.problems[:10]),
            priority="high",
            tags=["warning"],
            source="ops",
        )
        if dry_run:
            log.warning("(dry-run) would send: %s\n%s", alert.title, alert.body)
        else:
            dispatch(alert, {"ntfy": {"topic": resolve_topic("sys")}})
    log.info("일일 점검: engine=%s checked=%d problems=%d heartbeat=%s",
             report.engine, report.checked, len(report.problems), report.heartbeat)
    return report
