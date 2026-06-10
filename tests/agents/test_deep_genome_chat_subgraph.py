# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Dispatch tests for ``DeepGenomeReportMixin._dispatch_chat``.

Pins the chat-dispatch chokepoint that the report node body uses:
the dispatch routes through ``_cached_chat_app().ainvoke`` using the
shared chat_adapters IO mappers.
"""

# pylint: disable=protected-access
# Test file exercises ``DeepGenomeReportMixin._dispatch_chat`` (an
# internal helper that owns the chat-call seam shared by the
# experiment / protocol / discussion / summary / follow_up report
# nodes) directly to assert chat-subgraph dispatch.

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.deep_genome import report as report_module
from mcp_server_phytomni.config.defaults import DeepGenomeConfig

pytestmark = pytest.mark.agent


def _build_mixin_instance() -> Any:
    """Construct a minimal stand-in for ``DeepGenomeReportMixin``.

    ``_dispatch_chat`` only reads ``self.deep_genome_config`` and the
    shared ``_chat_kwargs`` builder, so a ``SimpleNamespace`` with a
    stub kwargs bag is enough to exercise the dispatch without
    constructing the full ``DeepGenomeAgents``.
    """
    config = DeepGenomeConfig()
    return SimpleNamespace(
        deep_genome_config=config,
        _chat_kwargs=lambda: {"chat-kwargs-stub": True},
    )


async def test_dispatch_chat_uses_subgraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatch delegates to the compiled chat subgraph."""
    mixin = _build_mixin_instance()
    subgraph_app_mock = AsyncMock(
        return_value={
            "response": {"choices": [{"message": {"content": "sub"}}]}
        }
    )
    monkeypatch.setattr(
        report_module,
        "_cached_chat_app",
        lambda: SimpleNamespace(ainvoke=subgraph_app_mock),
    )

    result = await report_module.DeepGenomeReportMixin._dispatch_chat(
        mixin, "prompt-stub"
    )

    assert result == {"choices": [{"message": {"content": "sub"}}]}
    subgraph_app_mock.assert_awaited_once()
