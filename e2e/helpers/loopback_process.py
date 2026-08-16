# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared subprocess lifecycle for loopback E2E services."""

from __future__ import annotations

import subprocess
import threading
from collections import deque
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import IO


@dataclass(frozen=True)
class LoopbackProcessConfig:
    """Lifecycle bounds that differ between loopback test services."""

    log_tail_lines: int
    termination_timeout_seconds: float
    drain_timeout_seconds: float = 5.0


class BoundedLogTail:
    """Expose a fixed-size subprocess tail without its mutable deque."""

    def __init__(self, max_lines: int) -> None:
        self._max_lines = max_lines
        self._lines: deque[str] = deque(maxlen=max_lines)
        self._lock = threading.Lock()

    @property
    def max_lines(self) -> int:
        """Return the immutable configured diagnostic bound."""
        return self._max_lines

    def record(self, line: str) -> None:
        """Append one drained line for the owning process helper only."""
        with self._lock:
            self._lines.append(line)

    def snapshot(self) -> tuple[str, ...]:
        """Return an immutable point-in-time diagnostic tail."""
        with self._lock:
            return tuple(self._lines)

    def __len__(self) -> int:
        """Return the current bounded diagnostic line count."""
        with self._lock:
            return len(self._lines)


def _drain(stream: IO[str], logs: BoundedLogTail) -> None:
    """Keep a subprocess pipe flowing into its caller-bounded log tail."""
    for line in stream:
        logs.record(line.rstrip("\n"))


@contextmanager
def boot_loopback_process(
    command: list[str],
    environment: dict[str, str],
    base_url: str,
    health_check: Callable[[subprocess.Popen[str], str, BoundedLogTail], None],
    config: LoopbackProcessConfig,
) -> Generator[BoundedLogTail, None, None]:
    """Run one loopback child with bounded logs and deterministic teardown."""
    logs = BoundedLogTail(config.log_tail_lines)
    with subprocess.Popen(
        command,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ) as process:
        assert process.stdout is not None
        drain = threading.Thread(
            target=_drain, args=(process.stdout, logs), daemon=True
        )
        drain.start()
        try:
            health_check(process, base_url, logs)
            yield logs
        finally:
            process.terminate()
            try:
                process.wait(timeout=config.termination_timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
    drain.join(timeout=config.drain_timeout_seconds)


__all__ = ["BoundedLogTail", "LoopbackProcessConfig", "boot_loopback_process"]
