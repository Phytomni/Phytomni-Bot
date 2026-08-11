# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Citation database configuration for real e2e server subprocesses."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from scripts.citation_db import build_citation_database

_CITATION_DB_PATH = "CITATION_DB_PATH"
_PHYTOMNI_CITATION_DB_PATH = "PHYTOMNI_CITATION_DB_PATH"


def _nonblank_environment(name: str) -> bool:
    """Return whether an operator supplied a nonblank path alias."""
    value = os.environ.get(name)
    return value is not None and bool(value.strip())


@contextmanager
def configured_e2e_citation_database(
    root: Path,
    *,
    force_disposable: bool = False,
) -> Iterator[Path | None]:
    """Preserve an operator path or provide one empty valid artifact.

    ``force_disposable`` is reserved for explicitly gated non-production
    probes.  It prevents a stale local operator path from leaking into a
    disposable subprocess while leaving ordinary e2e runs operator-owned.
    """
    if not force_disposable and (
        _nonblank_environment(_CITATION_DB_PATH)
        or _nonblank_environment(_PHYTOMNI_CITATION_DB_PATH)
    ):
        yield None
        return

    previous_path = os.environ.get(_CITATION_DB_PATH)
    owned_root = Path(tempfile.mkdtemp(prefix="citation-db-", dir=root))
    source = owned_root / "empty.jsonl"
    database = owned_root / "citation.sqlite"
    try:
        source.touch(exist_ok=False)
        build_citation_database(source, database)
        os.environ[_CITATION_DB_PATH] = str(database)
        yield database
    finally:
        if previous_path is None:
            os.environ.pop(_CITATION_DB_PATH, None)
        else:
            os.environ[_CITATION_DB_PATH] = previous_path
        database.unlink(missing_ok=True)
        source.unlink(missing_ok=True)
        owned_root.rmdir()
