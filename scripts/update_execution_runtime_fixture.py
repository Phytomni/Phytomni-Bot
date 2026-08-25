#!/usr/bin/env python3
"""Refresh generated capability sections in the Runtime V2 fixture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mcp_server_phytomni.api.agent_capabilities import (
    serialize_execution_runtime_capability,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.fixture.read_text(encoding="utf-8"))
    payload["capabilities"][
        "operation_records"
    ] = serialize_execution_runtime_capability()["operation_records"]
    args.fixture.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
