#!/usr/bin/env python3
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Report missing CI configuration names without exposing their values."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

from mcp_server_phytomni.config.required_env import missing_environment


def main(argv: Sequence[str] | None = None) -> int:
    """Check the supplied names against the process environment.

    Args:
        argv: Names to check. When omitted, names are read from the command
            line after the script path.

    Returns:
        ``1`` when any name is missing; otherwise ``0``.

    Notes:
        Only variable names and a count are printed. Environment values are
        intentionally never included in output.
    """
    names = tuple(argv if argv is not None else sys.argv[1:])
    missing = missing_environment(names, os.environ)
    if missing:
        print("Missing required CI configuration: " + ", ".join(missing))
        return 1
    print(f"Required CI configuration present: {len(names)} names")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
