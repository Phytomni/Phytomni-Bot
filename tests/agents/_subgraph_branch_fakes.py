# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared fixtures for analyst-subgraph dispatch flag tests.

Centralises the mock-installation and dispatcher-construction
boilerplate every ``test_*_analyst_subgraph.py`` file would
otherwise repeat, so the design / network / research dispatcher
test files stay below pylint's ``R0801`` ``min-similar-lines``
threshold while still exercising each dispatcher's flag branch.
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
    monkeypatch.setattr(f"{module_path}.submit_analyst_analysis", legacy_mock)
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
    use_subgraph: bool,
) -> Any:
    """Construct a dispatcher agent with ``USE_ANALYST_SUBGRAPH`` set.

    Builds the dispatcher's config via ``model_copy`` (avoids pylint
    C0103 invalid-name on direct UPPERCASE attribute assignment) and
    wires a ``SimpleNamespace`` analyst stub so the dispatcher
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
        use_subgraph: Initial ``USE_ANALYST_SUBGRAPH`` value.

    Returns:
        A dispatcher instance wired with the analyst stub and a
        ``SensitiveConfig.load()`` real instance (tests run under
        the ``tests/conftest.py`` ``_TEST_ENV``).
    """
    config = config_cls().model_copy(
        update={"USE_ANALYST_SUBGRAPH": use_subgraph}
    )
    analyst_stub = SimpleNamespace(identifier=lambda: "stub-analyst")
    return agent_cls(
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, analyst_stub),
        **{config_kwarg: config},
    )
