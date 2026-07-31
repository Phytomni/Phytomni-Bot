# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Atomic temporary-artifact helpers for routing report publication."""

from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path
from uuid import uuid4


def write_temporary(path: Path, content: str) -> Path:
    """Write one flushed temporary artifact beside its destination."""
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        with suppress(FileNotFoundError):
            temporary.unlink()
        raise
    return temporary


def remove_temporary(path: Path | None) -> None:
    """Remove a publication temporary file when it exists."""
    if path is not None:
        with suppress(FileNotFoundError):
            path.unlink()


def restore_destination(path: Path, previous: bytes | None) -> None:
    """Restore one destination after a pair publication failure."""
    if previous is None:
        with suppress(FileNotFoundError):
            path.unlink()
        return
    path.write_bytes(previous)
