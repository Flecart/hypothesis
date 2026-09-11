from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from .api import ApiError, HypothesisClient
from .config import load_config
from .state import StateStore
from .sync import Synchronizer
from .vault import VaultError, discover


def _project_config() -> Path:
    return Path(__file__).resolve().parents[2] / "config.toml"


def _parse_date(value: str, timezone_name: str, *, today: date | None = None) -> date:
    today = today or __import__("datetime").datetime.now(ZoneInfo(timezone_name)).date()
    if value == "today":
        return today
    if value == "yesterday":
        return today - timedelta(days=1)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise RuntimeError("date must be today, yesterday, or YYYY-MM-DD") from exc


def _sync_dates(date_value: str | None, since_value: str | None, timezone_name: str,
                *, today: date | None = None) -> list[date]:
    today = today or __import__("datetime").datetime.now(ZoneInfo(timezone_name)).date()
    if since_value is None:
        return [_parse_date(date_value or "today", timezone_name, today=today)]
    start = _parse_date(since_value, timezone_name, today=today)
    if start > today:
        raise RuntimeError("--since cannot be later than today")
    return [start + timedelta(days=offset) for offset in range((today - start).days + 1)]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hypothesis-export")
    parser.add_argument("--config", type=Path, default=_project_config())
    subparsers = parser.add_subparsers(dest="command", required=True)
    sync = subparsers.add_parser("sync", help="synchronize Hypothesis and Obsidian")
    date_group = sync.add_mutually_exclusive_group()
    date_group.add_argument("--date", help="one date: today, yesterday, or YYYY-MM-DD")
    date_group.add_argument("--since", help="first date to sync through today, inclusive")
    sync.add_argument("--dry-run", action="store_true")
    subparsers.add_parser("status", help="show synchronization and marker status")
    return parser


def _print_result(result, dry_run: bool) -> None:
    prefix = "dry-run: " if dry_run else ""
    print(prefix + " ".join([
        f"fetched={result.fetched}", f"created={result.created_entries}",
        f"updated={result.updated_entries}", f"patched={result.patched}",
        f"moved={result.moved_entries}", f"detached={result.detached_entries}",
        f"remote_missing={result.remote_missing}", f"files={result.written_files}",
    ]))
    for message in result.messages:
        print(message)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        store = StateStore(config.state_path)
        try:
            if args.command == "status":
                marker_state = discover(config.vault)
                print(json.dumps({
                    "database": store.counts(),
                    "discovered_entries": len(marker_state.entries),
                    "discovered_sources": len(marker_state.sources),
                    "errors": marker_state.errors,
                }, indent=2, sort_keys=True))
                return 1 if marker_state.errors else 0
            token = os.environ.get("APP_TOKEN")
            if not token:
                raise RuntimeError("APP_TOKEN is not set")
            client = HypothesisClient(config.api_url, token)
            days = _sync_dates(args.date, args.since, config.timezone)
            synchronizer = Synchronizer(config, client, store)
            for index, day in enumerate(days):
                if len(days) > 1:
                    print(f"=== sync {day.isoformat()} ({index + 1}/{len(days)}) ===")
                result = synchronizer.sync(day, dry_run=args.dry_run)
                _print_result(result, args.dry_run)
            if len(days) > 1:
                print(f"range-complete since={days[0].isoformat()} through={days[-1].isoformat()} days={len(days)}")
            return 0
        finally:
            store.close()
    except (ApiError, VaultError, RuntimeError, OSError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
