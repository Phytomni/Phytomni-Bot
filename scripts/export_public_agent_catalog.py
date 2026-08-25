#!/usr/bin/env python3
"""Export the canonical public-agent catalog as deterministic JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mcp_server_phytomni.public_agent_catalog import (
    export_public_agent_catalog,
)


def main() -> int:
    """Write the canonical catalog export to the requested path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(export_public_agent_catalog(), indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
