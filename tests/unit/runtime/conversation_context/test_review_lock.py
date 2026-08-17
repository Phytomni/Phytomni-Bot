# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Review mutation lock acquire and double-release paths."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from mcp_server_phytomni.runtime.conversation_context.review_lock import (
    ReviewMutationLock,
    ReviewMutationLockTimeoutError,
    acquire_review_mutation_lock,
)

pytestmark = pytest.mark.unit


def test_review_mutation_lock_release_is_idempotent() -> None:
    """A second release after the first is a no-op."""
    released: list[int] = []
    local = SimpleNamespace(release=lambda: released.append(1))
    lock = ReviewMutationLock(local_lock=cast(Any, local))
    lock.release()
    lock.release()
    assert released == [1]


def test_acquire_review_mutation_lock_times_out_on_memory_path() -> None:
    """An already-held in-memory lock times out instead of blocking forever."""
    held = acquire_review_mutation_lock(":memory:", timeout=None)
    try:
        with pytest.raises(ReviewMutationLockTimeoutError):
            acquire_review_mutation_lock(":memory:", timeout=0.0)
    finally:
        held.release()


def test_acquire_review_mutation_lock_uses_file_descriptor(
    tmp_path,
) -> None:
    """POSIX file locks return a releasable file-descriptor lock."""
    path = tmp_path / "review.lock"
    path.write_text("", encoding="utf-8")
    with acquire_review_mutation_lock(path, timeout=1.0) as lock:
        assert getattr(lock, "_file_descriptor") is not None
    assert getattr(lock, "_file_descriptor") is None
