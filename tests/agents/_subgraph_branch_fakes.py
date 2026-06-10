# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared fixtures for subgraph-dispatch flag tests.

Centralises the mock-installation and dispatcher-construction
boilerplate every ``test_*_analyst_subgraph.py`` and
``test_*_chat_subgraph.py`` file would otherwise repeat, so the
per-consumer flag-branch test files stay below pylint's ``R0801``
``min-similar-lines`` threshold while still exercising each
consumer's flag branch.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.analyst.agent import AnalystAgent
from mcp_server_phytomni.config.settings import SensitiveConfig


def install_branch_mocks(
    monkeypatch: pytest.MonkeyPatch,
    module_path: str,
) -> tuple[AsyncMock, AsyncMock]:
    """Install legacy + subgraph async mocks on a dispatcher module.

    Patches both ``submit_analyst_analysis`` and
    ``submit_analyst_via_subgraph`` on the dispatcher's import
    namespace so flag-on / flag-off branch tests can monkeypatch
    them in one call and observe which one runs.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        module_path: Dotted import path of the dispatcher module
            whose ``submit_analyst_analysis`` and
            ``submit_analyst_via_subgraph`` names should be replaced
            (e.g. ``"mcp_server_phytomni.agents.design.agent"``).

    Returns:
        A ``(legacy_mock, subgraph_mock)`` tuple — both ``AsyncMock``
        instances pre-loaded with deterministic ``task_id`` payloads
        so callers can assert which branch was awaited without
        further setup.
    """
    legacy_mock = AsyncMock(return_value={"task_id": "legacy-task"})
    subgraph_mock = AsyncMock(return_value={"task_id": "subgraph-task"})
    # ``raising=False`` so consumer modules that retired the flag-off
    # ``submit_analyst_analysis`` import still drive the structural
    # subgraph-mount tests; the returned legacy mock stays a
    # never-awaited no-op there so ``assert_branch_taken(subgraph=True)``
    # keeps reading correctly.
    monkeypatch.setattr(
        f"{module_path}.submit_analyst_analysis", legacy_mock, raising=False
    )
    monkeypatch.setattr(
        f"{module_path}.submit_analyst_via_subgraph", subgraph_mock
    )
    return legacy_mock, subgraph_mock


def stub_prompt_parts(monkeypatch: pytest.MonkeyPatch, agent: Any) -> None:
    """Replace the dispatcher's ``_analysis_prompt_parts`` with a stub.

    The flag-branch tests do not exercise prompt-template
    expansion or species data-list lookups — only which dispatch
    helper runs. A constant 3-tuple stub keeps the
    ``_dispatch_and_wait_analysis`` call body deterministic.
    """
    monkeypatch.setattr(
        agent,
        "_analysis_prompt_parts",
        lambda *_a, **_kw: ("goal", "meta", {}),
    )


def assert_branch_taken(
    result: Any,
    legacy_mock: AsyncMock,
    subgraph_mock: AsyncMock,
    *,
    subgraph: bool,
) -> None:
    """Assert exactly one branch ran and produced the expected payload.

    Args:
        result: Return value of the dispatcher entry point.
        legacy_mock: Mock that replaced ``submit_analyst_analysis``.
        subgraph_mock: Mock that replaced ``submit_analyst_via_subgraph``.
        subgraph: ``True`` if the flag-on path was expected to run,
            ``False`` for the legacy default path.
    """
    if subgraph:
        assert result == {"task_id": "subgraph-task"}
        subgraph_mock.assert_awaited_once()
        legacy_mock.assert_not_awaited()
    else:
        assert result == {"task_id": "legacy-task"}
        legacy_mock.assert_awaited_once()
        subgraph_mock.assert_not_awaited()


def build_branch_agent(
    config_cls: type,
    agent_cls: type,
    config_kwarg: str,
) -> Any:
    """Construct a dispatcher agent wired with a stub analyst.

    Wires a ``SimpleNamespace`` analyst stub so the dispatcher
    instantiation never reaches a real ``AnalystAgent`` constructor.

    Args:
        config_cls: Dispatcher config class
            (``DigitalDesignConfig`` / ``GeneNetworkConfig``).
        agent_cls: Dispatcher agent class
            (``DigitalDesignAgents`` / ``GeneNetworkAgents``).
        config_kwarg: Keyword name the dispatcher's ``__init__``
            uses for its config object (e.g.
            ``"digital_design_config"`` for design,
            ``"gene_network_config"`` for network).

    Returns:
        A dispatcher instance wired with the analyst stub and a
        ``SensitiveConfig.load()`` real instance (tests run under
        the ``tests/conftest.py`` ``_TEST_ENV``).
    """
    analyst_stub = SimpleNamespace(identifier=lambda: "stub-analyst")
    return agent_cls(
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, analyst_stub),
        **{config_kwarg: config_cls()},
    )


def install_chat_branch_mocks(
    monkeypatch: pytest.MonkeyPatch,
    module_path: str,
    *,
    legacy_response: dict[str, Any] | None = None,
    subgraph_response: dict[str, Any] | None = None,
) -> tuple[AsyncMock, Any]:
    """Install legacy ``phyto_chat`` + compiled-chat-subgraph async mocks.

    Mirrors :func:`install_branch_mocks` but for chat consumers
    (knowledge / data / analyst / review / brief_gene / research /
    environment / evolution / deep_genome). Chat consumers do not
    split dispatch across two helpers the way analyst dispatchers do;
    they call ``phyto_chat(...)`` directly on the flag-off path and
    are expected to call ``_cached_chat_app().ainvoke(...)`` (via an
    ``adapter_node`` wrapper) on the flag-on path. This helper
    patches both names on the consumer module's namespace so a
    flag-branch test installs both candidates in one call and
    observes which one was awaited.

    Contract for the consumer module wiring (any future chat-branch
    consumer must honor this so the helper can patch deterministically):

    * The consumer module imports ``phyto_chat`` into its own
      namespace (e.g. ``from ..chat.service import phyto_chat``) so
      ``monkeypatch.setattr(f"{module_path}.phyto_chat", ...)`` reaches
      the binding the node actually calls.
    * The consumer module resolves the compiled chat subgraph through
      a ``_cached_chat_app`` name imported into its own namespace
      (e.g. ``from ..chat.service import _cached_chat_app``), called
      as ``_cached_chat_app()``. The returned object exposes an
      ``ainvoke`` coroutine. Going through this single indirection
      keeps the patch surface stable across consumers and avoids
      having to stub ``SubgraphRegistry.get_or_compile`` per-test
      (``build_default_registry()`` returns a fresh registry per call,
      so per-test registry stubbing would not intercept inline
      lookups).

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        module_path: Dotted import path of the consumer module whose
            ``phyto_chat`` and ``_cached_chat_app`` names should be
            replaced (e.g.
            ``"mcp_server_phytomni.agents.review.agent"``).
        legacy_response: Optional payload returned by the legacy
            ``phyto_chat`` mock. Defaults to a deterministic
            ``{"answer": "legacy-chat"}`` so callers can assert which
            branch was awaited without further setup.
        subgraph_response: Optional payload returned by the chat
            subgraph's ``ainvoke`` mock. Defaults to a deterministic
            ``{"answer": "subgraph-chat"}``.

    Returns:
        A ``(legacy_mock, subgraph_app_mock)`` tuple. ``legacy_mock``
        replaces ``phyto_chat``; ``subgraph_app_mock`` is the compiled
        chat subgraph stub returned by the patched
        ``_cached_chat_app()`` and exposes an ``ainvoke`` ``AsyncMock``
        pre-loaded with ``subgraph_response``. Tests assert
        ``legacy_mock.assert_awaited_once()`` versus
        ``subgraph_app_mock.ainvoke.assert_awaited_once()`` to verify
        which branch the consumer took.
    """
    legacy_mock = AsyncMock(
        return_value=legacy_response or {"answer": "legacy-chat"}
    )
    subgraph_app_mock = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value=subgraph_response or {"answer": "subgraph-chat"}
        )
    )
    # ``raising=False`` so consumer modules that retired the legacy
    # ``phyto_chat`` import (when their flag-off branch was deleted)
    # still drive the structural-mount tests; the returned legacy mock
    # stays a never-awaited no-op in that case so callers'
    # ``legacy_mock.assert_not_awaited()`` keeps reading correctly.
    monkeypatch.setattr(
        f"{module_path}.phyto_chat", legacy_mock, raising=False
    )
    monkeypatch.setattr(
        f"{module_path}._cached_chat_app", lambda: subgraph_app_mock
    )
    return legacy_mock, subgraph_app_mock


def assert_chat_branch_taken(
    result: Any,
    legacy_mock: AsyncMock,
    subgraph_app_mock: Any,
    *,
    subgraph: bool,
    expected: dict[str, Any] | None = None,
) -> None:
    """Assert exactly one chat branch ran and produced its payload.

    Args:
        result: Return value of the consumer node or wrapper under
            test.
        legacy_mock: Mock that replaced ``phyto_chat``.
        subgraph_app_mock: Stub returned by the patched
            ``_cached_chat_app`` — its ``ainvoke`` attribute is the
            ``AsyncMock`` to assert against.
        subgraph: ``True`` if the flag-on path was expected to run,
            ``False`` for the legacy default path.
        expected: Optional override of the payload to match against
            ``result``. Defaults to the same deterministic
            ``{"answer": "subgraph-chat"}`` / ``{"answer": "legacy-chat"}``
            stubs :func:`install_chat_branch_mocks` installs, so
            callers that did not override the response payload can
            omit this arg.
    """
    expected_payload = expected or (
        {"answer": "subgraph-chat"} if subgraph else {"answer": "legacy-chat"}
    )
    if subgraph:
        assert result == expected_payload
        subgraph_app_mock.ainvoke.assert_awaited_once()
        legacy_mock.assert_not_awaited()
    else:
        assert result == expected_payload
        legacy_mock.assert_awaited_once()
        subgraph_app_mock.ainvoke.assert_not_awaited()


def install_chat_subgraph_mocks(
    monkeypatch: pytest.MonkeyPatch,
    module_path: str,
    *,
    legacy_response: dict[str, Any] | None,
    subgraph_response: dict[str, Any] | None,
) -> tuple[AsyncMock, SimpleNamespace]:
    """Install legacy ``phyto_chat`` + shared ``CHAT_APP`` async mocks.

    Sibling of :func:`install_chat_branch_mocks` for the structural
    chat-subgraph mount pattern: consumer agents register a chat node
    via ``add_node(make_chat_node_wrapper(...))``, and the wrapper
    closes over the module-level ``CHAT_APP`` constant on
    :mod:`mcp_server_phytomni.agents.shared.chat_subgraph`. To exercise
    the flag-on path in consumer tests we must patch ``CHAT_APP`` at
    the shared module (where the wrapper reads it) rather than at the
    consumer module (which never references it once the registration
    runs at graph-build time).

    Contract for the consumer module wiring:

    * The consumer module still imports ``phyto_chat`` into its own
      namespace so the flag-off path stays patchable via
      ``monkeypatch.setattr(f"{module_path}.phyto_chat", ...)``.
    * The consumer module does NOT need to expose ``CHAT_APP`` itself;
      the structural wrapper resolves it from the shared module's
      globals at every ``ainvoke`` call.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        module_path: Dotted import path of the consumer module whose
            ``phyto_chat`` name should be replaced for the flag-off
            path (e.g.
            ``"mcp_server_phytomni.agents.knowledge.agent"``).
        legacy_response: Payload returned by the patched
            ``phyto_chat`` mock. Pass ``None`` to install a deterministic
            ``{"answer": "legacy-chat"}`` default.
        subgraph_response: Value placed under the ``response`` key of
            the ``ChatOutput`` dict the patched ``CHAT_APP.ainvoke``
            returns. Pass ``None`` for the null-response edge case
            (``ChatOutput`` declares ``response: Optional[...]``); the
            ainvoke return is ``{"response": None}`` in that case, not
            an empty dict, so consumers that lift ``out["response"]``
            still observe the documented contract.

    Returns:
        A ``(phyto_chat_mock, fake_chat_app)`` tuple. ``phyto_chat_mock``
        replaces the consumer module's ``phyto_chat`` binding;
        ``fake_chat_app`` replaces
        ``agents.shared.chat_subgraph.CHAT_APP`` and exposes an
        ``ainvoke`` ``AsyncMock`` pre-loaded with the configured
        ``ChatOutput`` payload. Tests assert
        ``phyto_chat_mock.assert_awaited_once()`` versus
        ``fake_chat_app.ainvoke.assert_awaited_once()`` to verify
        which branch the consumer took.
    """
    phyto_chat_mock = AsyncMock(
        return_value=legacy_response or {"answer": "legacy-chat"}
    )
    fake_chat_app = SimpleNamespace(
        ainvoke=AsyncMock(return_value={"response": subgraph_response})
    )
    # ``raising=False`` so consumer modules that retired the legacy
    # ``phyto_chat`` import (when their flag-off branch was deleted)
    # still drive the structural-mount tests; the returned legacy
    # mock stays a never-awaited no-op in that case so callers'
    # ``legacy_mock.assert_not_awaited()`` keeps reading correctly.
    monkeypatch.setattr(
        f"{module_path}.phyto_chat", phyto_chat_mock, raising=False
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.shared.chat_subgraph.CHAT_APP",
        fake_chat_app,
    )
    return phyto_chat_mock, fake_chat_app


def assert_chat_subgraph_branch_taken(
    result: Any,
    legacy_mock: AsyncMock,
    fake_chat_app: SimpleNamespace,
    *,
    subgraph: bool,
    expected: dict[str, Any] | None = None,
) -> None:
    """Assert exactly one structural chat branch ran.

    Sibling of :func:`assert_chat_branch_taken` for the structural
    mount pattern: branch selection is observed via the consumer's
    local ``phyto_chat`` mock (flag-off) versus the shared
    ``CHAT_APP.ainvoke`` mock (flag-on).

    Args:
        result: Return value of the consumer node or wrapper under
            test.
        legacy_mock: Mock that replaced the consumer module's
            ``phyto_chat``.
        fake_chat_app: ``SimpleNamespace`` returned by
            :func:`install_chat_subgraph_mocks` — its ``ainvoke``
            attribute is the ``AsyncMock`` to assert against.
        subgraph: ``True`` if the flag-on structural path was expected
            to run, ``False`` for the legacy direct-call path.
        expected: Optional payload to match against ``result``. When
            omitted, only the await-counts are checked; the result
            value is not compared. Pass the consumer's expected
            downstream payload to pin its exact shape.
    """
    if subgraph:
        fake_chat_app.ainvoke.assert_awaited_once()
        legacy_mock.assert_not_awaited()
    else:
        legacy_mock.assert_awaited_once()
        fake_chat_app.ainvoke.assert_not_awaited()
    if expected is not None:
        assert result == expected
