# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Admin CLI for the per-user API key store.

Functions: main.

Subcommands: create (prints the plaintext key exactly once), list
(metadata only, never the secret), revoke (disables a key by prefix).
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from ..config.defaults import ApiConfig
from .auth import ApiKeyStore, get_key_store

__all__ = ["main"]


def _store() -> ApiKeyStore:
    """Return the configured key store (env-overridable path)."""
    return get_key_store(ApiConfig().API_KEYS_DB_PATH)


def _cmd_create(args: argparse.Namespace) -> int:
    """Mint a key and print the one-time plaintext."""
    expires_at = None
    if args.expires_days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(
            days=args.expires_days
        )
    created = _store().create(
        user_id=args.user_id, name=args.name, expires_at=expires_at
    )
    print(
        "API key created. Store it now; it is shown only once and "
        "cannot be recovered."
    )
    print(f"  key:    {created.api_key}")
    print(f"  prefix: {created.prefix}")
    print(f"  user:   {created.user_id}")
    if args.name:
        print(f"  name:   {args.name}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    """Print non-secret metadata for stored keys."""
    records = _store().list(user_id=args.user_id)
    if not records:
        print("(no keys)")
        return 0
    header = (
        "PREFIX        USER            ACTIVE  CREATED_AT"
        "                        NAME"
    )
    print(header)
    for record in records:
        print(
            f"{record.prefix:<13} {record.user_id:<15} "
            f"{str(record.active):<7} {record.created_at:<25} "
            f"{record.name or '-'}"
        )
    return 0


def _cmd_revoke(args: argparse.Namespace) -> int:
    """Revoke an active key by its public prefix."""
    revoked = _store().revoke(args.prefix)
    print("revoked" if revoked else "no active key with that prefix")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for the key admin CLI."""
    parser = argparse.ArgumentParser(
        prog="phytomni-api-key",
        description="Manage per-user HTTP API keys.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Mint a new API key.")
    create.add_argument("--user-id", required=True)
    create.add_argument("--name", default=None)
    create.add_argument("--expires-days", type=int, default=None)
    create.set_defaults(func=_cmd_create)

    listing = sub.add_parser("list", help="List keys (no secrets).")
    listing.add_argument("--user-id", default=None)
    listing.set_defaults(func=_cmd_list)

    revoke = sub.add_parser("revoke", help="Revoke a key by prefix.")
    revoke.add_argument("--prefix", required=True)
    revoke.set_defaults(func=_cmd_revoke)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the API key admin CLI.

    Args:
        argv: Optional argument vector; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 on success).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
