# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Review mutation lock fallback, timeout, and unexpected-error edges."""

# pylint: disable=protected-access

from __future__ import annotations

import os
import threading

import pytest

from mcp_server_phytomni.runtime.conversation_context import review_lock
from mcp_server_phytomni.runtime.conversation_context.review_lock import (
    ReviewMutationLock,
    ReviewMutationLockTimeoutError,
    acquire_review_mutation_lock,
)

pytestmark = pytest.mark.unit


def test_local_lock_release_and_second_release_are_safe() -> None:
    """In-memory locks release once and ignore a later release."""
    local = threading.Lock()
    assert local.acquire(blocking=False) is True
    lock = ReviewMutationLock(local_lock=local)
    lock.release()
    lock.release()
    assert local.acquire(blocking=False) is True
    local.release()
    empty = ReviewMutationLock()
    empty.release()
    empty.release()


def test_file_descriptor_release_without_fcntl(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A held descriptor still closes when POSIX flock is unavailable."""
    path = tmp_path / "review.lock"
    path.write_text("", encoding="utf-8")
    file_descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    monkeypatch.setattr(review_lock, "fcntl", None)
    lock = ReviewMutationLock(file_descriptor=file_descriptor)
    lock.release()
    assert lock._file_descriptor is None
    with pytest.raises(OSError):
        os.close(file_descriptor)


def test_acquire_uses_memory_lock_when_fcntl_missing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing flock support falls back to a process-local lock map."""
    path = tmp_path / "memory-fallback.lock"
    monkeypatch.setattr(review_lock, "fcntl", None)
    with acquire_review_mutation_lock(path, timeout=1.0) as held:
        with pytest.raises(ReviewMutationLockTimeoutError):
            acquire_review_mutation_lock(path, timeout=0.0)
        assert held._local_lock is not None
    with acquire_review_mutation_lock(path, timeout=None) as lock:
        assert lock._local_lock is not None


def test_acquire_times_out_on_memory_path() -> None:
    """An already-held ``:memory:`` lock times out immediately."""
    unique = ":memory:review-lock-edges"
    held = acquire_review_mutation_lock(unique, timeout=None)
    try:
        with pytest.raises(ReviewMutationLockTimeoutError):
            acquire_review_mutation_lock(unique, timeout=0.0)
    finally:
        held.release()


def test_file_lock_timeout_and_unexpected_oserror(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retryable flock errors time out; other OS errors re-raise."""
    path = tmp_path / "flock.lock"
    path.write_text("", encoding="utf-8")

    def blocked(_descriptor: int, _flags: int) -> None:
        raise BlockingIOError()

    monkeypatch.setattr(review_lock.fcntl, "flock", blocked)
    with pytest.raises(ReviewMutationLockTimeoutError):
        acquire_review_mutation_lock(path, timeout=0.0)

    def eagain(_descriptor: int, _flags: int) -> None:
        raise OSError(11, "resource temporarily unavailable")

    monkeypatch.setattr(review_lock.fcntl, "flock", eagain)
    with pytest.raises(ReviewMutationLockTimeoutError):
        acquire_review_mutation_lock(path, timeout=0.0)

    def unexpected(_descriptor: int, _flags: int) -> None:
        raise OSError(5, "input/output error")

    monkeypatch.setattr(review_lock.fcntl, "flock", unexpected)
    with pytest.raises(OSError) as raised:
        acquire_review_mutation_lock(path, timeout=1.0)
    assert raised.value.errno == 5
