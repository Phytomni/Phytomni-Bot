#!/usr/bin/env python3
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Produce a per-customer encrypted .env for image distribution.

Operator-only build-time tool. Reads a plaintext .env, seals it with
a per-customer license key via the AES-256-GCM secret envelope, and
writes the .env.encrypted artifact baked into that customer's image.

This script exposes `main` as the CLI entrypoint and `parse_args` for
testing. It imports the envelope from the installed package, so run it
from a venv where `mcp_server_phytomni` is installed (editable is
fine, per the documented setup).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from mcp_server_phytomni.config import encrypt_env_file


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Optional explicit argument vector (used by tests).

    Returns:
        The parsed argparse namespace.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Path to the plaintext .env to encrypt.",
    )
    parser.add_argument(
        "--license-key",
        required=True,
        help="Per-customer license key used to derive the AES key.",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Destination path for the .env.encrypted blob.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the output file if it already exists.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Encrypt a plaintext .env into a per-customer envelope.

    Args:
        argv: Optional explicit argument vector (used by tests).

    Returns:
        Process exit code: 0 on success, non-zero on error.
    """
    args = parse_args(argv)

    if not args.input.is_file():
        print(f"error: input not found: {args.input}", file=sys.stderr)
        return 2
    if args.output.exists() and not args.force:
        print(
            f"error: output exists (use --force): {args.output}",
            file=sys.stderr,
        )
        return 3

    encrypt_env_file(args.input, args.license_key, args.output)

    blob = args.output.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    print(f"wrote {args.output} ({len(blob)} bytes) sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
