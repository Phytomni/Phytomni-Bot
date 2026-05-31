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

import mcp_server_phytomni.agents.shared.chat_subgraph as shared_chat_subgraph

from ._subgraph_branch_fakes import (
    assert_chat_branch_taken,
    assert_chat_subgraph_branch_taken,
    install_chat_branch_mocks,
    install_chat_subgraph_mocks,
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


def _make_fake_structural_consumer_module(
    monkeypatch: pytest.MonkeyPatch,
) -> str:
    """Register a consumer module exposing only the legacy ``phyto_chat`` seam.

    The structural-mount flag-on path does NOT read a
    ``_cached_chat_app`` symbol from the consumer module; it goes
    through the shared module-level ``CHAT_APP`` instead. So this
    helper only needs to register the ``phyto_chat`` name on a fake
    consumer module — the structural mock helper patches ``CHAT_APP``
    at the shared module's path independently.
    """
    module_name = "phyto_test_fake_structural_chat_consumer"
    module = ModuleType(module_name)

    async def _legacy(*_a: Any, **_kw: Any) -> dict[str, Any]:
        return {"answer": "real-legacy"}

    setattr(module, "phyto_chat", _legacy)
    monkeypatch.setitem(sys.modules, module_name, module)
    return module_name


async def test_install_chat_subgraph_mocks_patches_both_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Helper patches consumer ``phyto_chat`` AND shared ``CHAT_APP``."""
    module_path = _make_fake_structural_consumer_module(monkeypatch)

    legacy_mock, fake_chat_app = install_chat_subgraph_mocks(
        monkeypatch,
        module_path,
        legacy_response=None,
        subgraph_response=None,
    )

    consumer_module = sys.modules[module_path]
    assert getattr(consumer_module, "phyto_chat") is legacy_mock
    assert isinstance(legacy_mock, AsyncMock)

    assert shared_chat_subgraph.CHAT_APP is fake_chat_app
    assert isinstance(fake_chat_app, SimpleNamespace)
    assert isinstance(fake_chat_app.ainvoke, AsyncMock)


async def test_install_chat_subgraph_mocks_subgraph_response_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fake ``CHAT_APP.ainvoke`` returns a ``ChatOutput`` dict shape."""
    module_path = _make_fake_structural_consumer_module(monkeypatch)
    response = {"choices": [{"message": {"content": "hello"}}]}

    _legacy_mock, fake_chat_app = install_chat_subgraph_mocks(
        monkeypatch,
        module_path,
        legacy_response=None,
        subgraph_response=response,
    )

    chat_output = await fake_chat_app.ainvoke({"user_query": "Q"})
    assert chat_output == {"response": response}


async def test_install_chat_subgraph_mocks_legacy_response_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patched ``phyto_chat`` mock returns ``legacy_response`` verbatim."""
    module_path = _make_fake_structural_consumer_module(monkeypatch)
    legacy_payload = {"answer": "legacy-direct", "extra": 7}

    legacy_mock, _fake_chat_app = install_chat_subgraph_mocks(
        monkeypatch,
        module_path,
        legacy_response=legacy_payload,
        subgraph_response=None,
    )

    assert await legacy_mock(user_query="probe") == legacy_payload


async def test_install_chat_subgraph_mocks_null_subgraph_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passing ``subgraph_response=None`` yields ``{"response": None}``."""
    module_path = _make_fake_structural_consumer_module(monkeypatch)

    _legacy_mock, fake_chat_app = install_chat_subgraph_mocks(
        monkeypatch,
        module_path,
        legacy_response=None,
        subgraph_response=None,
    )

    chat_output = await fake_chat_app.ainvoke({"user_query": "Q"})
    assert chat_output == {"response": None}


async def test_assert_chat_subgraph_branch_taken_subgraph_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subgraph-only awaits classify cleanly; legacy await trips the check."""
    module_path = _make_fake_structural_consumer_module(monkeypatch)
    response = {"choices": [{"message": {"content": "sub"}}]}
    legacy_mock, fake_chat_app = install_chat_subgraph_mocks(
        monkeypatch,
        module_path,
        legacy_response=None,
        subgraph_response=response,
    )

    chat_output = await fake_chat_app.ainvoke({"user_query": "Q"})
    consumer_result = chat_output["response"]

    assert_chat_subgraph_branch_taken(
        consumer_result, legacy_mock, fake_chat_app, subgraph=True
    )

    # If the legacy mock also ran, the subgraph-branch assertion fails.
    await legacy_mock()
    with pytest.raises(AssertionError):
        assert_chat_subgraph_branch_taken(
            consumer_result, legacy_mock, fake_chat_app, subgraph=True
        )


async def test_assert_chat_subgraph_branch_taken_legacy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy-only awaits classify cleanly; subgraph await trips the check."""
    module_path = _make_fake_structural_consumer_module(monkeypatch)
    legacy_payload = {"answer": "legacy-direct"}
    legacy_mock, fake_chat_app = install_chat_subgraph_mocks(
        monkeypatch,
        module_path,
        legacy_response=legacy_payload,
        subgraph_response=None,
    )

    consumer_result = await legacy_mock(user_query="probe")

    assert_chat_subgraph_branch_taken(
        consumer_result, legacy_mock, fake_chat_app, subgraph=False
    )

    # If the subgraph also ran, the legacy-branch assertion fails.
    await fake_chat_app.ainvoke({"user_query": "Q"})
    with pytest.raises(AssertionError):
        assert_chat_subgraph_branch_taken(
            consumer_result, legacy_mock, fake_chat_app, subgraph=False
        )


async def test_assert_chat_subgraph_branch_taken_expected_value_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``expected`` gates result equality independently of branch counts."""
    module_path = _make_fake_structural_consumer_module(monkeypatch)
    response = {"choices": [{"message": {"content": "sub"}}]}
    legacy_mock, fake_chat_app = install_chat_subgraph_mocks(
        monkeypatch,
        module_path,
        legacy_response=None,
        subgraph_response=response,
    )

    chat_output = await fake_chat_app.ainvoke({"user_query": "Q"})
    consumer_result = chat_output["response"]

    # Matching ``expected`` passes.
    assert_chat_subgraph_branch_taken(
        consumer_result,
        legacy_mock,
        fake_chat_app,
        subgraph=True,
        expected=response,
    )

    # Mismatched ``expected`` fails even when the branch counts line up.
    with pytest.raises(AssertionError):
        assert_chat_subgraph_branch_taken(
            consumer_result,
            legacy_mock,
            fake_chat_app,
            subgraph=True,
            expected={"answer": "different"},
        )
