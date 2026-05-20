# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Admin CLI for the func_cache SQLite store.

Functions: main.

Subcommands: ``stats`` (per-func live entry counts), ``purge`` (drop
entries for one func or all funcs), ``reexpire`` (rewrite TTL with
``--ttl SECONDS`` or drop expiry with ``--permanent``), ``purge-
expired`` (sweep already-expired rows).
"""

from __future__ import annotations

import argparse
from typing import Optional, Sequence

from ..common.cli import dispatch_cli
from .maintenance import (
    cache_stats,
    purge_all,
    purge_expired_entries,
    purge_func,
    reexpire_all,
    reexpire_func,
)

__all__ = ["main"]


def _cmd_stats(args: argparse.Namespace) -> int:
    """Print live entry counts for every func in the cache."""
    stats = cache_stats(db_path=args.db_path)
    if not stats:
        print("(no cache entries)")
        return 0
    width = max(len(func_id) for func_id in stats)
    print(f"{'FUNC_ID':<{width}}  COUNT")
    for func_id in sorted(stats):
        print(f"{func_id:<{width}}  {stats[func_id]}")
    return 0


def _cmd_purge(args: argparse.Namespace) -> int:
    """Drop cache entries for one func or every func."""
    if args.func_id is None:
        purged = purge_all(db_path=args.db_path)
        print(f"purged {purged} entries across all funcs")
    else:
        purged = purge_func(args.func_id, db_path=args.db_path)
        print(f"purged {purged} entries from {args.func_id}")
    return 0


def _cmd_reexpire(args: argparse.Namespace) -> int:
    """Rewrite expires_at on entries for one func or every func."""
    new_ttl = None if args.permanent else args.ttl
    if args.func_id is None:
        rowcount = reexpire_all(new_ttl, db_path=args.db_path)
        print(f"reexpired {rowcount} entries across all funcs")
    else:
        rowcount = reexpire_func(args.func_id, new_ttl, db_path=args.db_path)
        print(f"reexpired {rowcount} entries from {args.func_id}")
    return 0


def _cmd_purge_expired(args: argparse.Namespace) -> int:
    """Sweep already-expired rows out of the cache database."""
    purge_expired_entries(db_path=args.db_path)
    print("expired entries purged")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for the func_cache admin CLI."""
    parser = argparse.ArgumentParser(
        prog="phytomni-cache",
        description="Manage the func_cache SQLite store.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Explicit cache SQLite path (defaults to PHYTOMNI_CACHE_DB).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    stats = sub.add_parser("stats", help="Live entry count per func.")
    stats.set_defaults(func=_cmd_stats)

    purge = sub.add_parser("purge", help="Drop cache entries.")
    purge.add_argument(
        "--func-id",
        default=None,
        help="Restrict to one func (default: every func).",
    )
    purge.set_defaults(func=_cmd_purge)

    reexpire = sub.add_parser(
        "reexpire", help="Rewrite expires_at on live entries."
    )
    ttl_group = reexpire.add_mutually_exclusive_group(required=True)
    ttl_group.add_argument(
        "--ttl",
        type=float,
        default=None,
        help="New TTL in seconds (time.time() + ttl).",
    )
    ttl_group.add_argument(
        "--permanent",
        action="store_true",
        help="Drop expires_at so matched rows never expire.",
    )
    reexpire.add_argument(
        "--func-id",
        default=None,
        help="Restrict to one func (default: every func).",
    )
    reexpire.set_defaults(func=_cmd_reexpire)

    purge_expired = sub.add_parser(
        "purge-expired", help="Sweep already-expired rows."
    )
    purge_expired.set_defaults(func=_cmd_purge_expired)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the func_cache admin CLI."""
    return dispatch_cli(_build_parser, argv)


if __name__ == "__main__":
    raise SystemExit(main())
