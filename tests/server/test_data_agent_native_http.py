# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Native DataAgent HTTP stage-trace contract tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from tests.agents._subgraph_branch_fakes import install_chat_subgraph_mocks
from tests.support.subgraph_fakes import install_knowledge_app

from mcp_server_phytomni.agents.data import agent as data_agent_module
from mcp_server_phytomni.agents.data.agent import DataAgent
from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.api.routes import agents as agent_routes_module
from mcp_server_phytomni.config.defaults import DataConfig
from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.mcp.formatting.dispatch import (
    build_tool_result_envelope,
)
from mcp_server_phytomni.runtime.stage_trace import (
    DataStage,
    StageTraceEvent,
    trace_data_stage,
)

pytestmark = pytest.mark.server

_DATA_MODULE = "mcp_server_phytomni.agents.data.agent"
_CHAT_RESPONSE = {
    "choices": [{"message": {"content": "rewritten query"}}],
}


def _capture_data_trace(
    monkeypatch: pytest.MonkeyPatch,
    events: list[StageTraceEvent],
) -> None:
    """Route every DataAgent trace event into a test-local list."""
    original = trace_data_stage
    sink = SimpleNamespace(emit=events.append)

    def captured(stage: DataStage, *, dependency: str, **kwargs: Any) -> Any:
        """Return the production context manager with a test sink."""
        kwargs["sink"] = sink
        return original(stage, dependency=dependency, **kwargs)

    monkeypatch.setattr(data_agent_module, "trace_data_stage", captured)
    monkeypatch.setattr(api_app_module, "trace_data_stage", captured)
    monkeypatch.setattr(agent_routes_module, "trace_data_stage", captured)


def _install_data_graph(
    monkeypatch: pytest.MonkeyPatch,
    *,
    database_error: BaseException | None = None,
) -> tuple[DataAgent, Any]:
    """Build one deterministic DataAgent graph with no external services."""
    install_knowledge_app(monkeypatch, f"{_DATA_MODULE}.build_knowledge_app")
    _, fake_chat_app = install_chat_subgraph_mocks(
        monkeypatch,
        _DATA_MODULE,
        legacy_response=None,
        subgraph_response=_CHAT_RESPONSE,
    )

    async def fake_execute(_request: Any) -> dict[str, Any]:
        """Return a small table or raise the requested upstream error."""
        if database_error is not None:
            raise database_error
        return {
            "header": [{"name": "gene"}],
            "data": [["AT1G01010"]],
        }

    monkeypatch.setattr(
        data_agent_module,
        "execute_nl2sql_request",
        fake_execute,
    )
    agent = DataAgent(
        data_config=DataConfig(),
        sensitive_config=SensitiveConfig.load(),
    )
    return agent, fake_chat_app


async def _post_data_run(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
) -> httpx.Response:
    """Post the smallest valid native DataAgent request."""
    return await api_client.post(
        "/v1/agents/data/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={"arguments": {"user_query": "How many genes?"}},
    )


async def test_native_data_run_records_all_six_stages(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The native route records the six stages in completion order."""
    events: list[StageTraceEvent] = []
    _capture_data_trace(monkeypatch, events)
    agent, _fake_chat_app = _install_data_graph(monkeypatch)

    async def fake_invoke(_tool_name: str, arguments: dict[str, Any]) -> Any:
        """Drive the real DataAgent graph through the shared API seam."""
        payload = await agent.arun(
            user_query=arguments["user_query"],
            is_rewrite=True,
            locale=arguments.get("locale"),
        )
        return build_tool_result_envelope(
            "DataAgent",
            payload,
            arguments=arguments,
        )

    monkeypatch.setattr(api_app_module, "invoke_tool_enveloped", fake_invoke)

    response = await _post_data_run(api_client, issued_api_key)

    assert response.status_code == 200
    assert tuple(event.stage for event in events) == tuple(
        stage.value for stage in DataStage
    )
    assert all(event.error_code is None for event in events)
    assert "AT1G01010" not in response.json()["result"]["formatted"]["answer"]


async def test_data_rewrite_timeout_projects_public_stage(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rewrite timeout is exposed as a safe data_rewrite failure."""
    events: list[StageTraceEvent] = []
    _capture_data_trace(monkeypatch, events)
    _agent, fake_chat_app = _install_data_graph(monkeypatch)
    fake_chat_app.ainvoke.side_effect = TimeoutError("private prompt")

    response = await _post_data_run(api_client, issued_api_key)

    assert response.status_code == 504
    detail = response.json()["error"]
    assert detail["code"] == "upstream_timeout"
    assert detail["stage"] == "data_rewrite"
    assert detail["message"] == "upstream service timed out"
    assert "private prompt" not in response.text
    assert [event.stage for event in events] == [
        "native_request",
        "data_rewrite",
    ]
    assert events[-1].error_code == "upstream_timeout"


async def test_data_database_timeout_projects_public_stage(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A database timeout is exposed at the actual external-query boundary."""
    events: list[StageTraceEvent] = []
    _capture_data_trace(monkeypatch, events)
    _agent, _fake_chat_app = _install_data_graph(
        monkeypatch,
        database_error=TimeoutError("private database detail"),
    )

    response = await _post_data_run(api_client, issued_api_key)

    assert response.status_code == 504
    detail = response.json()["error"]
    assert detail["code"] == "upstream_timeout"
    assert detail["stage"] == "database_query"
    assert "private database detail" not in response.text
    assert [event.stage for event in events] == [
        "native_request",
        "data_rewrite",
        "nl2sql_request",
        "database_query",
    ]


async def test_data_nl2sql_input_failure_projects_public_stage(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NL2SQL request construction failures carry nl2sql_request."""
    events: list[StageTraceEvent] = []
    _capture_data_trace(monkeypatch, events)
    _agent, _fake_chat_app = _install_data_graph(monkeypatch)

    def fail_request(_cls: Any, *_args: Any, **_kwargs: Any) -> Any:
        """Raise a safe input error before the external query starts."""
        del _cls
        raise ValueError("private NL2SQL request detail")

    monkeypatch.setattr(
        data_agent_module.Nl2SqlRequest,
        "from_kwargs",
        fail_request,
    )

    response = await _post_data_run(api_client, issued_api_key)

    assert response.status_code == 400
    detail = response.json()["error"]
    assert detail["code"] == "invalid_stage_input"
    assert detail["stage"] == "nl2sql_request"
    assert "private NL2SQL request detail" not in response.text
    assert [event.stage for event in events] == [
        "native_request",
        "data_rewrite",
        "nl2sql_request",
    ]


async def test_data_result_format_failure_projects_public_stage(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Formatter failures carry the result_format stage to the client."""
    events: list[StageTraceEvent] = []
    _capture_data_trace(monkeypatch, events)

    async def fake_invoke(*_args: Any, **_kwargs: Any) -> Any:
        """Return a minimal envelope before the formatter fails."""
        return build_tool_result_envelope(
            "DataAgent",
            {"header": [], "data": []},
        )

    monkeypatch.setattr(api_app_module, "invoke_tool_enveloped", fake_invoke)

    def fail_format(*_args: Any, **_kwargs: Any) -> Any:
        """Raise a payload-free formatter failure for the boundary test."""
        raise ValueError("private formatter detail")

    monkeypatch.setattr(
        api_app_module,
        "_format_agent_run_result",
        fail_format,
    )

    response = await _post_data_run(api_client, issued_api_key)

    assert response.status_code == 400
    detail = response.json()["error"]
    assert detail["code"] == "invalid_stage_input"
    assert detail["stage"] == "result_format"
    assert "private formatter detail" not in response.text
    assert [event.stage for event in events] == [
        "native_request",
        "result_format",
    ]


async def test_data_run_persistence_failure_projects_public_stage(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persistence failures keep the completed run out of succeeded state."""
    events: list[StageTraceEvent] = []
    _capture_data_trace(monkeypatch, events)

    async def fake_invoke(*_args: Any, **_kwargs: Any) -> Any:
        """Return a valid result before persistence is attempted."""
        return build_tool_result_envelope(
            "DataAgent",
            {"header": [], "data": []},
        )

    def fail_record_sync(**_kwargs: Any) -> str:
        """Raise the private persistence detail at the traced boundary."""
        raise api_app_module.run_lifecycle.RunPersistenceError(
            "private persistence detail"
        )

    monkeypatch.setattr(api_app_module, "invoke_tool_enveloped", fake_invoke)
    monkeypatch.setattr(api_app_module, "_record_sync_run", fail_record_sync)

    response = await _post_data_run(api_client, issued_api_key)

    assert response.status_code == 500
    detail = response.json()["error"]
    assert detail["code"] == "run_persistence_failed"
    assert detail["stage"] == "run_persist"
    assert "private persistence detail" not in response.text
    assert [event.stage for event in events] == [
        "native_request",
        "result_format",
        "run_persist",
    ]
    assert events[-1].error_code == "stage_failed"
