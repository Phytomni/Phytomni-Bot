# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Flag-branch tests for the environment VCI analyst-subgraph dispatch.

Asserts ``submit_vci_task_node`` calls ``agent.submit`` directly when
``USE_ANALYST_SUBGRAPH=False`` and routes through
``submit_analyst_via_subgraph`` when ``True``. Both branches are
pinned without constructing a real ``AnalystAgent``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.environment import graph as environment_graph
from mcp_server_phytomni.agents.environment.graph import submit_vci_task_node
from mcp_server_phytomni.agents.environment.state import EnvironmentState

pytestmark = pytest.mark.agent


def _state_with_codes() -> EnvironmentState:
    """Build an EnvironmentState ready for ``submit_vci_task_node``.

    The extract step has populated ``region_codes``; ``submit_vci_task_node``
    only reads region_codes plus the ``kwargs`` / ``batch`` overrides
    so the surrounding state stays minimal.
    """
    return {
        "query": "VCI analysis for Beijing Haidian",
        "region_codes": ["110000", "110100", "110108"],
        "kwargs": {"user_id": "user-test"},
        "batch": True,
    }


def _install_legacy_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncMock:
    """Patch the prompt / data / submit dependencies for the legacy path."""

    async def fake_submit(**kwargs: Any) -> dict[str, Any]:
        return {"task_id": "legacy-vci-task", "submit_kwargs": kwargs}

    submit_mock = AsyncMock(side_effect=fake_submit)
    monkeypatch.setattr(environment_graph.agent, "submit", submit_mock)
    monkeypatch.setattr(
        environment_graph.agent,
        "get_prompt",
        lambda *_a, **_kw: "prompt-stub",
    )
    monkeypatch.setattr(
        environment_graph.agent,
        "get_data_list",
        lambda *_a, **_kw: ["obs://data/vci-1"],
    )
    return submit_mock


async def test_submit_vci_task_uses_legacy_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default flag-off path awaits the legacy ``agent.submit``.

    Pins the production-default routing: ``submit_vci_task_node`` must
    call ``analyst.submit`` directly and the subgraph helper must not
    run. ``USE_ANALYST_SUBGRAPH`` defaults to ``False`` on
    ``EnvironmentConfig`` so the default state of the env config
    object is what production sees.
    """
    monkeypatch.setattr(
        environment_graph.ENVIRONMENT_CONFIG, "USE_ANALYST_SUBGRAPH", False
    )
    legacy_mock = _install_legacy_submit(monkeypatch)
    subgraph_mock = AsyncMock(return_value={"task_id": "subgraph-vci-task"})
    monkeypatch.setattr(
        environment_graph, "submit_analyst_via_subgraph", subgraph_mock
    )

    result = await submit_vci_task_node(_state_with_codes())

    assert result["vci_analysis_task"]["task_id"] == "legacy-vci-task"
    legacy_mock.assert_awaited_once()
    subgraph_mock.assert_not_awaited()


async def test_submit_vci_task_uses_subgraph_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-on path delegates to ``submit_analyst_via_subgraph``.

    The opt-in path forwards the goal / data / output_dir / meta into
    the dispatch request and bypasses the legacy ``agent.submit`` call;
    the test asserts both observable conditions so the flag's
    behavior is binary.
    """
    monkeypatch.setattr(
        environment_graph.ENVIRONMENT_CONFIG, "USE_ANALYST_SUBGRAPH", True
    )
    legacy_mock = _install_legacy_submit(monkeypatch)
    subgraph_mock = AsyncMock(
        return_value={
            "task_id": "subgraph-vci-task",
            "output_dir": "obs://run/vci-out",
            "task_status": "SUCCEEDED",
        }
    )
    monkeypatch.setattr(
        environment_graph, "submit_analyst_via_subgraph", subgraph_mock
    )
    # _build_submit_agent must be stubbed because constructing a real
    # AnalystAgent reaches into cached-agent registry + IAM token
    # acquisition, neither of which is available offline.
    monkeypatch.setattr(
        environment_graph,
        "_build_submit_agent",
        lambda *_a, **_kw: ("analyst-agent-stub", "", "small", "thread-x"),
    )

    result = await submit_vci_task_node(_state_with_codes())

    assert result["vci_analysis_task"]["task_id"] == "subgraph-vci-task"
    subgraph_mock.assert_awaited_once()
    legacy_mock.assert_not_awaited()


async def test_submit_vci_subgraph_request_carries_target_and_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-on path packs the region codes into ``target_id`` and pins polling.

    The subgraph helper builds the dispatch request from the resolved
    province / city / county codes and threads ``is_polling=False`` so
    the analyst graph runs fire-and-poll-elsewhere just like the
    legacy ``analyst.submit`` path.
    """
    monkeypatch.setattr(
        environment_graph.ENVIRONMENT_CONFIG, "USE_ANALYST_SUBGRAPH", True
    )
    _install_legacy_submit(monkeypatch)
    subgraph_mock = AsyncMock(return_value={"task_id": "subgraph-vci-task"})
    monkeypatch.setattr(
        environment_graph, "submit_analyst_via_subgraph", subgraph_mock
    )
    monkeypatch.setattr(
        environment_graph,
        "_build_submit_agent",
        lambda *_a, **_kw: ("analyst-agent-stub", "", "small", "thread-x"),
    )

    await submit_vci_task_node(_state_with_codes())

    call_args = subgraph_mock.await_args
    assert call_args is not None
    request = call_args.args[3]
    assert request["analysis_type"] == "vci_analysis"
    assert request["target_id"] == "110000-110100-110108"
    assert request["prompt_parts"][0] == "prompt-stub"
    assert call_args.kwargs["is_polling"] is False
