# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared dispatch entry-point for admin CLIs.

Public functions:
- dispatch_cli: Parse ``argv`` with the supplied parser factory and run
  the subcommand handler stored on ``args.func`` (the standard
  ``set_defaults(func=...)`` pattern used by every admin CLI here).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence

__all__ = ["dispatch_cli"]


def dispatch_cli(
    parser_factory: Callable[[], argparse.ArgumentParser],
    argv: Sequence[str] | None = None,
) -> int:
    """Run an ``argparse``-driven admin CLI and return its exit code.

    Each admin CLI module (e.g. ``api/keys.py``, ``func_cache/cli.py``)
    builds an ``ArgumentParser`` and attaches a subcommand handler via
    ``subparser.set_defaults(func=...)``. This helper centralises the
    parse → dispatch → integer-return-code shape so every CLI's
    module-level ``main`` is a single delegation.

    Args:
        parser_factory: Zero-arg callable that returns a configured
            ``ArgumentParser`` with subcommand ``func`` defaults set.
        argv: Optional argument vector; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 on success).
    """
    parser = parser_factory()
    args = parser.parse_args(argv)
    return int(args.func(args))
