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


def _parse_date(value: str, timezone_name: str) -> date:
    today = __import__("datetime").datetime.now(ZoneInfo(timezone_name)).date()
    if value == "today":
        return today
    if value == "yesterday":
        return today - timedelta(days=1)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be today, yesterday, or YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hypothesis-export")
    parser.add_argument("--config", type=Path, default=_project_config())
    subparsers = parser.add_subparsers(dest="command", required=True)
    sync = subparsers.add_parser("sync", help="synchronize Hypothesis and Obsidian")
    sync.add_argument("--date", default="today", help="today, yesterday, or YYYY-MM-DD")
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
            day = _parse_date(args.date, config.timezone)
            client = HypothesisClient(config.api_url, token)
            result = Synchronizer(config, client, store).sync(day, dry_run=args.dry_run)
            _print_result(result, args.dry_run)
            return 0
        finally:
            store.close()
    except (ApiError, VaultError, RuntimeError, OSError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
