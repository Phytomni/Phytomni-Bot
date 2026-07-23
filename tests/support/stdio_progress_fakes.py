# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Neutral seam setup for MCP stdio progress tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from types import ModuleType

import pytest

__all__ = ["patch_stdio_progress_seams"]


def patch_stdio_progress_seams(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    fake_app: object,
    progress_stream: Callable[..., AsyncIterator[object]],
    terminal_payload: object,
) -> None:
    """Patch graph, progress, and terminal seams with neutral doubles."""
    monkeypatch.setattr(
        module,
        "_graph_stream_target",
        lambda _tool, _args: (fake_app, {"user_query": "q"}),
    )
    monkeypatch.setattr(module, "_astream_progress_ticks", progress_stream)
    monkeypatch.setattr(module, "_stdio_terminal_payload", terminal_payload)
