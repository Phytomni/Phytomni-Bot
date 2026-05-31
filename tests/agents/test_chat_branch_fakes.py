# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Smoke tests for the shared chat-subgraph branch mock helper.

Verifies :func:`install_chat_branch_mocks` patches both candidate
chat call sites on a target module's namespace, returns the correct
mock pair shape, and that :func:`assert_chat_branch_taken` correctly
classifies each branch's outcome.
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from ._subgraph_branch_fakes import (
    assert_chat_branch_taken,
    install_chat_branch_mocks,
)

pytestmark = pytest.mark.agent


def _make_fake_consumer_module(monkeypatch: pytest.MonkeyPatch) -> str:
    """Register a fake consumer module exposing both chat seams."""
    module_name = "phyto_test_fake_chat_consumer"
    module = ModuleType(module_name)

    async def _legacy(*_a: Any, **_kw: Any) -> dict[str, Any]:
        return {"answer": "real-legacy"}

    def _cached_chat_app() -> Any:
        return SimpleNamespace(
            ainvoke=AsyncMock(return_value={"answer": "real-subgraph"})
        )

    setattr(module, "phyto_chat", _legacy)
    setattr(module, "_cached_chat_app", _cached_chat_app)
    monkeypatch.setitem(sys.modules, module_name, module)
    return module_name


async def test_install_chat_branch_mocks_returns_async_mock_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Helper returns a 2-tuple wiring both chat call sites."""
    module_path = _make_fake_consumer_module(monkeypatch)

    legacy_mock, subgraph_app_mock = install_chat_branch_mocks(
        monkeypatch, module_path
    )

    assert isinstance(legacy_mock, AsyncMock)
    assert isinstance(subgraph_app_mock.ainvoke, AsyncMock)

    legacy_result = await legacy_mock(user_query="probe")
    subgraph_result = await subgraph_app_mock.ainvoke({"user_query": "probe"})
    assert legacy_result == {"answer": "legacy-chat"}
    assert subgraph_result == {"answer": "subgraph-chat"}


async def test_install_chat_branch_mocks_honors_custom_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Custom ``legacy_response`` / ``subgraph_response`` override defaults."""
    module_path = _make_fake_consumer_module(monkeypatch)

    legacy_mock, subgraph_app_mock = install_chat_branch_mocks(
        monkeypatch,
        module_path,
        legacy_response={"answer": "custom-legacy", "extra": 1},
        subgraph_response={"answer": "custom-subgraph", "extra": 2},
    )

    assert await legacy_mock() == {"answer": "custom-legacy", "extra": 1}
    sub_result = await subgraph_app_mock.ainvoke({})
    assert sub_result == {"answer": "custom-subgraph", "extra": 2}


async def test_assert_chat_branch_taken_classifies_each_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``assert_chat_branch_taken`` accepts legacy and subgraph outcomes."""
    module_path = _make_fake_consumer_module(monkeypatch)
    legacy_mock, subgraph_app_mock = install_chat_branch_mocks(
        monkeypatch, module_path
    )

    legacy_payload = await legacy_mock()
    assert_chat_branch_taken(
        legacy_payload, legacy_mock, subgraph_app_mock, subgraph=False
    )

    # Re-install fresh mocks so the second assertion sees a clean call count.
    legacy_mock, subgraph_app_mock = install_chat_branch_mocks(
        monkeypatch, module_path
    )
    subgraph_payload = await subgraph_app_mock.ainvoke({})
    assert_chat_branch_taken(
        subgraph_payload, legacy_mock, subgraph_app_mock, subgraph=True
    )
