# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Cross-worker Review mutation locks used by conversation context storage."""

from __future__ import annotations

import os
import threading
import time
from typing import Self

try:
    import fcntl
except ImportError:  # pragma: no cover - the supported runtime is POSIX.
    fcntl = None  # type: ignore[assignment]

__all__ = ["ReviewMutationLock", "ReviewMutationLockTimeoutError"]


class ReviewMutationLockTimeoutError(TimeoutError):
    """Raised when a Review mutation lock cannot be acquired in time."""


class ReviewMutationLock:
    """Releasable lock held across a private checkpoint write."""

    def __init__(
        self,
        *,
        file_descriptor: int | None = None,
        local_lock: threading.Lock | None = None,
    ) -> None:
        self._file_descriptor = file_descriptor
        self._local_lock = local_lock
        self._state_lock = threading.Lock()
        self._released = False

    def release(self) -> None:
        """Release the lock exactly once; release may run in another thread."""
        with self._state_lock:
            if self._released:
                return
            self._released = True
            file_descriptor = self._file_descriptor
            local_lock = self._local_lock
            self._file_descriptor = None
            self._local_lock = None
        if file_descriptor is not None:
            if fcntl is not None:
                fcntl.flock(file_descriptor, fcntl.LOCK_UN)
            os.close(file_descriptor)
        elif local_lock is not None:
            local_lock.release()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


_REVIEW_MUTATION_LOCKS: dict[str, threading.Lock] = {}
_REVIEW_MUTATION_LOCKS_GUARD = threading.Lock()


def acquire_review_mutation_lock(
    path: str | os.PathLike[str], timeout: float | None = 30.0
) -> ReviewMutationLock:
    """Acquire the process/file lock used around Review checkpoint writes."""
    normalized_path = os.fspath(path)
    if fcntl is None or normalized_path == ":memory:":
        with _REVIEW_MUTATION_LOCKS_GUARD:
            lock = _REVIEW_MUTATION_LOCKS.setdefault(
                normalized_path, threading.Lock()
            )
        acquired = lock.acquire(timeout=-1 if timeout is None else timeout)
        if not acquired:
            raise ReviewMutationLockTimeoutError(normalized_path)
        return ReviewMutationLock(local_lock=lock)

    file_descriptor = os.open(normalized_path, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return ReviewMutationLock(file_descriptor=file_descriptor)
            except (BlockingIOError, OSError) as exc:
                if not isinstance(exc, BlockingIOError) and getattr(
                    exc, "errno", None
                ) not in {11, 13}:
                    raise
                if deadline is not None and time.monotonic() >= deadline:
                    raise ReviewMutationLockTimeoutError(
                        normalized_path
                    ) from exc
                time.sleep(0.01)
    except BaseException:
        os.close(file_descriptor)
        raise
