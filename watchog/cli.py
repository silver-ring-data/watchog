"""watchog command line.

    python -m watchog test              send a test alert through every sink
    python -m watchog run               run every enabled watcher once
    python -m watchog run --only cgv    run one watcher
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys

from watchog.core import config as config_mod
from watchog.core.notify import Alert, dispatch
from watchog.core.state import SeenStore


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_test(cfg: dict, args: argparse.Namespace) -> int:
    alert = Alert(
        title="watchog 테스트",
        body="알림 경로가 정상입니다.\n이 알림이 보이면 설정 끝.",
        url="https://github.com/silver-ring-data/watchog",
        priority="high",
        tags=["eyes"],
        source="test",
    )
    delivered = dispatch(alert, cfg.get("sinks", {}))
    return 0 if delivered else 1


def cmd_run(cfg: dict, args: argparse.Namespace) -> int:
    watchers = cfg.get("watchers", {})
    if args.only:
        watchers = {k: v for k, v in watchers.items() if k == args.only}
        if not watchers:
            print(f"no watcher named {args.only!r}", file=sys.stderr)
            return 2

    state_path = cfg.get("state_path", "data/seen.json")
    failures = 0

    with SeenStore(state_path, cfg.get("retention_days", 90)) as seen:
        for name, wcfg in watchers.items():
            if wcfg.get("enabled") is False:
                continue
            try:
                module = importlib.import_module(f"watchog.watchers.{name}")
                alerts = module.check(wcfg, seen)
            except Exception:
                logging.exception("watcher %r failed", name)
                failures += 1
                continue

            for alert in alerts:
                dispatch(alert, cfg.get("sinks", {}))

    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="watchog")
    parser.add_argument("-c", "--config", default=config_mod.DEFAULT_PATH)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("test", help="send a test alert through every sink")

    run = sub.add_parser("run", help="run watchers once")
    run.add_argument("--only", help="run just this watcher")

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    cfg = config_mod.load(args.config)

    if args.command == "test":
        return cmd_test(cfg, args)
    return cmd_run(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
