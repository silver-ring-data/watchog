"""watchog command line.

    python -m watchog test                      send a test alert through every sink
    python -m watchog run [--only NAME]         run the self-contained watchers once
    python -m watchog recipes check             validate recipes/*.yaml
    python -m watchog recipes list              show recipes and their sources
    python -m watchog recipes sync [--dry-run]  push recipes to changedetection.io
    python -m watchog parse URL [SELECTOR]      preview what a source would yield
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys

from dotenv import find_dotenv, load_dotenv

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


def cmd_recipes(cfg: dict, args: argparse.Namespace) -> int:
    from watchog import recipes as recipes_mod

    try:
        found = recipes_mod.load_all(args.dir)
    except recipes_mod.RecipeError as e:
        print(f"레시피 오류: {e}", file=sys.stderr)
        return 2
    if args.action == "check":
        for r in found:
            print(f"ok  {r.name}  kind={r.kind} mode={r.mode} runner={r.runner} sources={len(r.sources)} verified={r.verified}")
        print(f"{len(found)}개 레시피 정상")
        return 0
    if args.action == "list":
        for r in found:
            print(f"{r.name}  [{r.topic}] {r.kind}/{r.mode} runner={r.runner}")
            for i, src in enumerate(r.sources):
                print(f"   {i}. {src.type:<10} {src.every:<4} {src.fetch:<8} {src.title or src.url}")
        return 0
    from watchog.cdio import ChangeDetection

    client = ChangeDetection.from_env()
    try:
        result = recipes_mod.sync(found, client, dry_run=args.dry_run)
    except recipes_mod.RecipeError as e:
        print(f"동기화 중단: {e}", file=sys.stderr)
        return 2
    prefix = "(dry-run) " if args.dry_run else ""
    for action, keys in result.items():
        if keys:
            print(f"{prefix}{action}: {', '.join(keys)}")
    return 0


def cmd_parse(cfg: dict, args: argparse.Namespace) -> int:
    from watchog import extract

    try:
        res = extract.test_parse(args.url, args.selector)
    except extract.ExtractError as e:
        print(f"[{e.category}] {e}", file=sys.stderr)
        return 1
    print(f"url: {res.url}")
    print(f"selector: {res.selector}")
    for n in res.notes:
        print(f"note: {n[:300]}")
    if res.candidates:
        for i, c in enumerate(res.candidates):
            print(f"candidate {i}: {c['selector']}  ({c['count']}건)  예: {' / '.join(c['sample'])[:120]}")
    print(f"items: {len(res.items)}")
    for it in res.items[:15]:
        print(f"  - {it[:100]}")
    return 0 if res.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="watchog")
    parser.add_argument("-c", "--config", default=config_mod.DEFAULT_PATH)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("test", help="send a test alert through every sink")

    run = sub.add_parser("run", help="run watchers once")
    run.add_argument("--only", help="run just this watcher")

    rec = sub.add_parser("recipes", help="manage recipes/*.yaml")
    rec.add_argument("action", choices=["check", "list", "sync"])
    rec.add_argument("--dir", default="recipes")
    rec.add_argument("--dry-run", action="store_true")

    parse = sub.add_parser("parse", help="preview what a URL yields (test_parse)")
    parse.add_argument("url")
    parse.add_argument("selector", nargs="?")

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    # .env holds the real secrets locally; on GitHub Actions the same names
    # arrive as repository secrets, so a missing file is not an error.
    load_dotenv(find_dotenv(usecwd=True))
    cfg = config_mod.load(args.config)

    if args.command == "test":
        return cmd_test(cfg, args)
    if args.command == "recipes":
        return cmd_recipes(cfg, args)
    if args.command == "parse":
        return cmd_parse(cfg, args)
    return cmd_run(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
