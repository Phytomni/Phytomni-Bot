# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Focused tests for loopback subprocess diagnostics."""

from __future__ import annotations

import os
import sys
import threading
from io import StringIO

from .helpers.loopback_process import (
    BoundedLogTail,
    LoopbackProcessConfig,
    _drain,
    boot_loopback_process,
)


def test_bounded_log_tail_exposes_only_read_only_snapshot() -> None:
    """A drain retains only its fixed-size, immutable diagnostic tail."""
    tail = BoundedLogTail(max_lines=2)
    _drain(StringIO("first\nsecond\nthird\n"), tail)

    snapshot = tail.snapshot()

    assert tail.max_lines == 2
    assert len(tail) == 2
    assert snapshot == ("second", "third")


def test_boot_yields_the_bounded_tail_shared_with_health_check() -> None:
    """The caller sees the same bounded object that receives child output."""
    observed: list[BoundedLogTail] = []

    def health_check(
        _process: object, _base_url: str, logs: BoundedLogTail
    ) -> None:
        observed.append(logs)

    command = [
        sys.executable,
        "-u",
        "-c",
        "import time; print('one'); print('two'); print('three'); time.sleep(1)",
    ]
    with boot_loopback_process(
        command,
        dict(os.environ),
        "http://127.0.0.1:1",
        health_check,
        LoopbackProcessConfig(log_tail_lines=2, termination_timeout_seconds=1),
    ) as log_tail:
        assert log_tail is observed[0]

    assert len(observed[0]) <= 2
    assert isinstance(observed[0].snapshot(), tuple)


def test_bounded_log_tail_stays_readable_while_a_drain_writes() -> None:
    """Concurrent diagnostic reads never expose mutable deque state."""
    tail = BoundedLogTail(max_lines=3)
    started = threading.Event()

    def drain_lines() -> None:
        started.set()
        _drain(StringIO("line\n" * 1000), tail)

    drain = threading.Thread(target=drain_lines)
    drain.start()
    assert started.wait(timeout=1)
    while drain.is_alive():
        assert len(tail) <= 3
        assert isinstance(tail.snapshot(), tuple)
    drain.join(timeout=1)

    assert not drain.is_alive()
    assert len(tail) <= 3
