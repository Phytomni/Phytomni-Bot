# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Synchronous, remote, background, and failure route tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path

from tests.server.test_query_route import (
    _BACKGROUND_EXPERT_CASES,
    _FORCED_ROUTE_CASES,
    _SYNC_EXPERT_CASES,
    Any,
    ApiKeyStore,
    RunRegistry,
    SimpleNamespace,
    ToolSelection,
    _auth,
    _patch_select,
    _post_query_route,
    _router_completion,
    _stub_tool_handler,
    _wait_for_run_children,
    api_app,
    httpx,
    pytest,
    server,
)
from tests.support.chat_fakes import install_chat_handler
from tests.support.expert_router_fakes import patch_expert_router

import mcp_server_phytomni.api.a2a.messages as a2a_messages
from mcp_server_phytomni.agents.expert import ToolSelectionError
from mcp_server_phytomni.agents.expert import router as expert_router
from mcp_server_phytomni.api.agent_run_support import (
    running_agent_run_response,
)
from mcp_server_phytomni.api.lifecycle_contract import empty_agent_result
from mcp_server_phytomni.config.defaults import ApiConfig, ServerConfig
from mcp_server_phytomni.mcp.formatting.agui import run_finished, run_started
from mcp_server_phytomni.mcp.schemas import AGENT_TOOL_DEFINITIONS
from mcp_server_phytomni.runtime.submit_recorder import records_submission
from mcp_server_phytomni.runtime.upload_registry import (
    UploadMetadata,
    UploadRegistry,
)
from mcp_server_phytomni.storage.obs_storage import obs_path_from_key

_STREAM_EXPERT_SLUGS = frozenset({"chat", "knowledge", "brief_gene"})


def _patch_expert_dispatch_seams(
    monkeypatch: pytest.MonkeyPatch,
    invoked: list[dict[str, Any]],
) -> None:
    """Capture blocking invoke and stream-family Expert dispatch."""

    async def fake_invoke(**kwargs: Any) -> tuple[dict[str, Any], int]:
        invoked.append(kwargs)
        return (
            {
                "id": f"route-{kwargs['agent']}",
                "object": "agent.run",
                "agent": kwargs["agent"],
                "status": "succeeded",
                "task_ids": [],
                "result": empty_agent_result(),
            },
            200,
        )

    async def fake_stream(**kwargs: Any) -> tuple[dict[str, Any], int]:
        invoked.append({"agent": kwargs["slug"], **kwargs})
        return running_agent_run_response(
            run_id=f"route-{kwargs['slug']}",
            agent=kwargs["slug"],
        )

    monkeypatch.setattr(api_app, "_invoke_agent_run", fake_invoke)
    monkeypatch.setattr(api_app, "_start_routed_expert_stream", fake_stream)


pytestmark = pytest.mark.server

_FORCED_NON_RESEARCH_CASES = tuple(
    case for case in _FORCED_ROUTE_CASES if case[0] != "InSilicoResearchAgent"
)
_BACKGROUND_NON_RESEARCH_CASES = tuple(
    case
    for case in _BACKGROUND_EXPERT_CASES
    if case.id != "research-background"
)


@pytest.fixture(name="scoped_key_without_agents")
def _scoped_key_without_agents_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    """Expose the scope-limited API-key fixture in this split module."""
    db = str(tmp_path / "keys.sqlite")
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", db)
    return ApiKeyStore(db).create(user_id="u9", scopes=["files"]).api_key


async def test_route_sync_agent_returns_resolved_slug(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A routed Knowledge stream returns its resolved slug and attachments.

    Locks HR-1 (``agent`` is the resolved slug, never ``"expert"``) and
    confirms the verbatim obs attachment reaches the knowledge stream.
    """
    started: list[dict[str, Any]] = []
    path = obs_path_from_key(
        ServerConfig().BUCKET_NAME,
        f"{ApiConfig().API_UPLOAD_PREFIX.strip('/')}/u1/expert/"
        "knowledge/context.pdf",
    )
    UploadRegistry(tasks_db_path).record(
        UploadMetadata(
            file_id="knowledge-context",
            user_id="u1",
            obs_path=path,
            filename="context.pdf",
            purpose="agent_context",
            byte_size=1_024,
            format="pdf",
            media_type="application/pdf",
            created_at="2026-07-25T00:00:00+00:00",
        )
    )

    async def prepared_stream(
        selected_tool: str,
        selected_arguments: dict[str, Any],
        *,
        run_id: str,
        dialogue_id: str | None,
        **_kwargs: Any,
    ) -> AsyncIterator[Any]:
        started.append(selected_arguments)
        yield run_started(run_id, dialogue_id)
        yield run_finished(run_id)

    monkeypatch.setattr(api_app, "prepare_tool_stream", prepared_stream)
    _patch_select(
        monkeypatch,
        ToolSelection("KnowledgeAgent", {"user_query": "rice drought"}),
    )

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "rice drought",
            "obs_file_list": [path],
            "allowed_tools": ["KnowledgeAgent"],
        },
    )
    assert response.status_code == 202
    body = response.json()
    assert body["object"] == "agent.run"
    assert body["agent"] == "knowledge"
    assert body["status"] == "running"
    assert body["task_ids"] == []
    assert body["id"] == body["run_id"]
    assert started[0]["obs_file_list"] == [path]
    assert started[0]["user_query"] == "rice drought"

    record = RunRegistry(tasks_db_path).get_run(body["run_id"], owner="u1")
    assert record is not None
    assert record.spec.agent == "knowledge"
    assert record.spec.origin == "local"


async def test_route_remote_agent_returns_reserved_run_before_child_ids(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A routed remote agent returns its umbrella before child persistence."""

    async def fake(args: Any) -> dict[str, Any]:
        _ = args
        return {"task_id": "T-A", "output_dir": "/obs/a"}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.ANALYST_AGENT.value,
        records_submission("analyst")(fake),
    )
    _patch_select(
        monkeypatch,
        ToolSelection(
            "AnalystAgent",
            {
                "goal_description": "assemble",
                "data_list": {},
                "obs_file_list": [],
            },
        ),
    )

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "assemble a genome",
            "allowed_tools": ["AnalystAgent"],
        },
    )
    assert response.status_code == 202
    body = response.json()
    assert body["agent"] == "analyst"
    assert body["status"] == "running"
    assert body["id"] == body["run_id"]
    assert body["task_ids"] == []
    assert body["result"] == empty_agent_result()
    request_id = response.headers["X-Request-Id"]

    record = await _wait_for_run_children(
        tasks_db_path,
        body["run_id"],
        {"T-A"},
    )
    assert record.spec.agent == "analyst"
    assert record.spec.origin == "remote"
    assert record.request_info.request_id == request_id


async def test_route_passes_constraints_to_selector_and_forces_agent(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forced request forwards its validated constraints to the selector."""
    captured: dict[str, object] = {}

    async def fake_select(
        user_query: str,
        history: Sequence[Mapping[str, Any]] = (),
        *,
        allowed_tools: Sequence[str] | None = None,
        forced_tool: str | None = None,
    ) -> ToolSelection:
        captured.update(
            {
                "user_query": user_query,
                "history": list(history),
                "allowed_tools": list(allowed_tools or []),
                "forced_tool": forced_tool,
            }
        )
        return ToolSelection(
            "DataAgent",
            {"user_query": "Compare drought candidates"},
        )

    _stub_tool_handler(
        monkeypatch,
        server.PhytomniAgents.DATA_AGENT.value,
        {"answer": "ok", "doc_list": []},
    )
    monkeypatch.setattr(api_app, "select_agent_tool", fake_select)

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "Compare drought candidates",
            "history": [{"role": "user", "content": "rice"}],
            "obs_file_list": [],
            "dialogue_id": "dialogue-1",
            "allowed_tools": ["ChatAgent", "DataAgent"],
            "forced_tool": "DataAgent",
        },
    )
    assert response.status_code == 202
    assert captured == {
        "user_query": "Compare drought candidates",
        "history": [{"role": "user", "content": "rice"}],
        "allowed_tools": ["ChatAgent", "DataAgent"],
        "forced_tool": "DataAgent",
    }
    assert response.json()["agent"] == "data"
    assert response.json()["status"] == "running"


@pytest.mark.parametrize("case", _FORCED_NON_RESEARCH_CASES)
async def test_route_forces_every_canonical_tool_to_its_native_slug(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str, dict[str, Any]],
) -> None:
    """Each shared canonical tool definition reaches its native slug."""
    tool_name, slug, arguments = case
    assert tuple(
        name.value for name, _description, _model in AGENT_TOOL_DEFINITIONS
    ) == tuple(case[0] for case in _FORCED_ROUTE_CASES)
    selector_call: dict[str, Any] = {}
    invoked: list[dict[str, Any]] = []
    _patch_expert_dispatch_seams(monkeypatch, invoked)
    _patch_select(
        monkeypatch,
        ToolSelection(tool_name, arguments),
        selector_call,
    )

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "q",
            "allowed_tools": [tool_name],
            "forced_tool": tool_name,
        },
    )

    expected_status = 202 if slug in _STREAM_EXPERT_SLUGS else 200
    assert response.status_code == expected_status
    assert len(invoked) == 1 and invoked[0]["agent"] == slug
    assert selector_call == {
        "user_query": "q",
        "history": [],
        "allowed_tools": [tool_name],
        "forced_tool": tool_name,
    }


@pytest.mark.parametrize("case", _BACKGROUND_NON_RESEARCH_CASES)
async def test_expert_background_selection_launches_one_reserved_worker(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
    case: tuple[str, str, dict[str, Any], dict[str, Any], set[str]],
) -> None:
    """Each background Expert selection uses one shared launcher call."""
    tool_name, slug, arguments, result, expected_task_ids = case
    launched: list[str] = []
    real_launch = api_app.launch_background_submission

    def capture_launch(
        reservation: Any, operation: Any, **kwargs: Any
    ) -> None:
        launched.append(reservation.agent)
        real_launch(reservation, operation, **kwargs)

    monkeypatch.setattr(
        api_app, "launch_background_submission", capture_launch
    )

    async def fake(_args: Any) -> dict[str, Any]:
        return result

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        {
            "analyst": server.PhytomniAgents.ANALYST_AGENT.value,
            "design": server.PhytomniAgents.DIGITAL_DESIGN_AGENT.value,
            "network": server.PhytomniAgents.GENE_NETWORK_AGENT.value,
        }[slug],
        records_submission(slug)(fake),
    )
    _patch_select(monkeypatch, ToolSelection(tool_name, arguments))

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "q",
            "allowed_tools": [tool_name],
            "forced_tool": tool_name,
        },
    )

    assert response.status_code == 202
    assert response.json()["agent"] == slug
    assert response.json()["status"] == "running"
    assert response.json()["id"] == response.json()["run_id"]
    assert response.json()["task_ids"] == []
    assert response.json()["result"] == empty_agent_result()
    assert launched == [slug]
    await _wait_for_run_children(
        tasks_db_path,
        response.json()["run_id"],
        expected_task_ids,
    )


@pytest.mark.parametrize("case", _SYNC_EXPERT_CASES)
async def test_expert_stream_selection_skips_background_launcher(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str, dict[str, Any]],
) -> None:
    """Stream-family Expert selections persist a running run without launch."""
    tool_name, slug, arguments = case
    launched: list[str] = []
    real_launch = api_app.launch_background_submission

    def capture_launch(
        reservation: Any, operation: Any, **kwargs: Any
    ) -> None:
        launched.append(reservation.agent)
        real_launch(reservation, operation, **kwargs)

    monkeypatch.setattr(
        api_app, "launch_background_submission", capture_launch
    )
    invoked: list[dict[str, Any]] = []
    _patch_expert_dispatch_seams(monkeypatch, invoked)
    _patch_select(monkeypatch, ToolSelection(tool_name, arguments))

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "q",
            "allowed_tools": [tool_name],
            "forced_tool": tool_name,
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["agent"] == slug
    assert body["status"] == "running"
    assert not launched
    assert len(invoked) == 1 and invoked[0]["agent"] == slug


async def test_route_autonomous_dispatches_one_allowed_tool(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Autonomous routing makes one constrained selection and dispatch."""
    captured: dict[str, Any] = {}
    invoked: list[dict[str, Any]] = []
    _patch_expert_dispatch_seams(monkeypatch, invoked)
    patch_expert_router(
        monkeypatch,
        expert_router,
        _router_completion(("ChatAgent", '{"user_query":"q"}')),
        captured,
    )

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "q",
            "allowed_tools": ["ReviewAgent", "ChatAgent"],
        },
    )

    assert response.status_code == 202
    assert len(invoked) == 1
    assert invoked[0]["agent"] == "chat"
    assert captured["tool_choice"] == "auto"
    assert [tool["function"]["name"] for tool in captured["tools"]] == [
        "ReviewAgent",
        "ChatAgent",
    ]


async def test_literal_agent_mention_stays_on_chat_surface(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Literal ``@DataAgent`` text does not invoke Expert routing."""
    captured: dict[str, Any] = {}
    install_chat_handler(monkeypatch, captured)

    async def forbidden_select(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("literal mentions must not enter Expert routing")

    monkeypatch.setattr(api_app, "select_agent_tool", forbidden_select)
    response = await api_client.post(
        "/v1/chat/completions",
        headers=_auth(issued_api_key),
        json={
            "model": "phyto-chat",
            "messages": [
                {
                    "role": "user",
                    "content": "Explain literal @DataAgent text",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["model"] == "phyto-chat"
    assert captured["user_query"] == "Explain literal @DataAgent text"


@pytest.mark.parametrize(
    "case",
    [
        (
            _router_completion(empty_choices=True),
            ["DataAgent", "KnowledgeAgent"],
            None,
        ),
        (
            _router_completion(),
            ["DataAgent", "KnowledgeAgent"],
            None,
        ),
        (
            _router_completion(("ChatAgent", "{}"), ("DataAgent", "{}")),
            ["ChatAgent", "DataAgent"],
            None,
        ),
        (
            _router_completion(("MissingAgent", "{}")),
            ["ChatAgent", "KnowledgeAgent"],
            None,
        ),
        (
            _router_completion(("DataAgent", "{}")),
            ["ChatAgent", "KnowledgeAgent"],
            None,
        ),
    ],
    ids=(
        "decline-no-chat",
        "decline-empty-no-chat",
        "multiple-calls",
        "unknown-call",
        "outside-allowlist",
    ),
)
async def test_route_strict_failures_never_invoke_agent(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[SimpleNamespace, list[str], str | None],
) -> None:
    """Non-forced strict selector contract failures stop before dispatch.

    A model *decline* (no choice / no tool call) is a 502 when ChatAgent is
    not allowlisted. Genuine violations -- multiple, unknown, or
    out-of-allowlist calls -- also always return 502 regardless of the
    allowlist. An unforced decline with ChatAgent allowed is covered by
    ``test_route_strict_decline_dispatches_chat_when_allowed``.

    A forced route is intentionally NOT a failure case here: a pinned
    ``@agent`` skips the routing model and dispatches the forced tool
    (see ``test_route_forced_tool_skips_routing_model``).
    """
    completion, allowed_tools, forced_tool = case
    invoked = 0

    async def forbidden_invoke(
        **_kwargs: object,
    ) -> tuple[dict[str, Any], int]:
        nonlocal invoked
        invoked += 1
        raise AssertionError("agent invocation must not run")

    monkeypatch.setattr(api_app, "_invoke_agent_run", forbidden_invoke)
    captured: dict[str, Any] = {}
    patch_expert_router(monkeypatch, expert_router, completion, captured)
    payload: dict[str, Any] = {
        "user_query": "q",
        "allowed_tools": allowed_tools,
    }
    if forced_tool is not None:
        payload["forced_tool"] = forced_tool
    response = await _post_query_route(api_client, issued_api_key, payload)
    assert response.status_code in {400, 422, 502}
    assert invoked == 0
    if forced_tool is not None:
        assert captured["tool_choice"] == {
            "type": "function",
            "function": {"name": forced_tool},
        }


async def test_route_forced_tool_skips_routing_model(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pinned tool dispatches without calling the routing model."""
    invoked: list[dict[str, Any]] = []

    async def explode(**_kwargs: Any) -> object:
        raise AssertionError("forced route must not call the routing model")

    _patch_expert_dispatch_seams(monkeypatch, invoked)
    monkeypatch.setattr(expert_router, "complete_expert_routing", explode)

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "q",
            "allowed_tools": ["KnowledgeAgent", "DataAgent"],
            "forced_tool": "KnowledgeAgent",
        },
    )

    assert response.status_code == 202
    assert len(invoked) == 1
    assert invoked[0]["agent"] == "knowledge"


async def test_route_singleton_allowlist_skips_routing_model(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One authorized tool dispatches without calling the routing model."""
    invoked: list[dict[str, Any]] = []

    async def explode(**_kwargs: Any) -> object:
        raise AssertionError(
            "singleton allowlist must not call the routing model"
        )

    _patch_expert_dispatch_seams(monkeypatch, invoked)
    monkeypatch.setattr(expert_router, "complete_expert_routing", explode)

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "q",
            "allowed_tools": ["KnowledgeAgent"],
        },
    )

    assert response.status_code == 202
    assert len(invoked) == 1
    assert invoked[0]["agent"] == "knowledge"


@pytest.mark.parametrize(
    "case",
    [
        ("ChatAgent", "chat", True),
        ("KnowledgeAgent", "knowledge", True),
        ("DataAgent", "data", False),
        ("ReviewAgent", "review", True),
        ("BriefGeneAgent", "brief_gene", False),
        ("AnalystAgent", "analyst", True),
        ("DeepGenomeAgent", "deep_genome", False),
        ("DigitalDesignAgent", "design", False),
        ("GeneNetworkAgent", "network", False),
    ],
)
async def test_route_attachment_forwarding_follows_capability_matrix(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
    case: tuple[str, str, bool],
) -> None:
    """Expert forwarding follows the registry's exact ten-tool matrix."""
    tool_name, slug, forwarded = case
    captured: dict[str, dict[str, Any]] = {}
    registry = UploadRegistry(tasks_db_path)
    file_id = "expert-context"
    path = obs_path_from_key(
        ServerConfig().BUCKET_NAME,
        f"{ApiConfig().API_UPLOAD_PREFIX.strip('/')}/u1/expert/"
        f"{file_id}/context.pdf",
    )
    registry.record(
        UploadMetadata(
            file_id=file_id,
            user_id="u1",
            obs_path=path,
            filename="context.pdf",
            purpose="agent_context",
            byte_size=1_024,
            format="pdf",
            media_type="application/pdf",
            created_at="2026-07-25T00:00:00+00:00",
        )
    )

    async def fake_invoke(
        *, agent: str, arguments: dict[str, Any], **_kwargs: Any
    ) -> tuple[dict[str, Any], int]:
        captured[agent] = arguments
        return (
            {
                "id": "r1",
                "object": "agent.run",
                "agent": agent,
                "status": "succeeded",
                "task_ids": [],
                "result": {"formatted": {}},
            },
            200,
        )

    async def fake_stream(
        *, slug: str, arguments: dict[str, Any], **_kwargs: Any
    ) -> tuple[dict[str, Any], int]:
        captured[slug] = arguments
        return running_agent_run_response(run_id="r1", agent=slug)

    monkeypatch.setattr(api_app, "_invoke_agent_run", fake_invoke)
    monkeypatch.setattr(api_app, "_start_routed_expert_stream", fake_stream)
    _patch_select(
        monkeypatch,
        ToolSelection(
            tool_name,
            {"user_query": "q", "obs_file_list": ["selector-private"]},
        ),
    )
    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "q",
            "obs_file_list": [path],
            "allowed_tools": [tool_name],
        },
    )
    if forwarded:
        expected_status = 202 if slug in _STREAM_EXPERT_SLUGS else 200
        if slug in {"data", "review"}:
            expected_status = 200
        assert response.status_code == expected_status
        assert captured[slug]["obs_file_list"] == [path]
    else:
        assert response.status_code == 422
        assert response.json()["error"]["code"] == ("attachment_not_supported")
        assert slug not in captured


async def test_route_requires_auth(
    api_client: httpx.AsyncClient,
) -> None:
    """An unauthenticated caller sees the unified 401."""
    response = await api_client.post(
        "/v1/query/route",
        json={"user_query": "hi", "allowed_tools": ["ChatAgent"]},
    )
    assert response.status_code == 401


async def test_route_insufficient_scope_returns_403(
    api_client: httpx.AsyncClient,
    scoped_key_without_agents: str,
) -> None:
    """A valid key lacking the ``agents`` scope is rejected with 403."""
    response = await _post_query_route(
        api_client,
        scoped_key_without_agents,
        {"user_query": "hi", "allowed_tools": ["ChatAgent"]},
    )
    assert response.status_code == 403


async def test_route_selection_failure_returns_sanitized_502(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selector contract failures never disclose routing inputs or output."""

    async def fake_select(*_args: Any, **_kwargs: Any) -> ToolSelection:
        raise ToolSelectionError(
            "DataAgent prohibited after model output: secret selection"
        )

    monkeypatch.setattr(api_app, "select_agent_tool", fake_select)
    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "hi",
            "allowed_tools": ["ChatAgent", "DataAgent"],
        },
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == ("routing_contract_violation")
    assert response.json()["error"]["stage"] == "routing"
    assert response.json()["error"]["retryable"] is False
    assert response.json()["error"]["message"] == (
        "The routing contract is invalid."
    )
    assert "DataAgent" not in response.text
    assert "secret selection" not in response.text


async def test_route_no_selection_returns_sanitized_502(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defensive ``selection is None`` branch stays a sanitized 502.

    In production strict ``select_agent_tool`` never returns ``None`` -- it
    raises ``ExpertRoutingDeclinedError`` on a decline -- so this exercises
    the
    defensive guard, which must not leak the query or allowlist even though
    ``ChatAgent`` is authorized. Both the sentinel and the typed decline are
    sanitized contract failures.
    """
    _patch_select(monkeypatch, None)

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "hi",
            "allowed_tools": ["ChatAgent", "DataAgent"],
        },
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == ("routing_contract_violation")
    assert response.json()["error"]["stage"] == "routing"
    assert response.json()["error"]["retryable"] is False
    assert response.json()["error"]["message"] == (
        "The routing contract is invalid."
    )
    assert "ChatAgent" not in response.text
    assert "DataAgent" not in response.text


async def test_legacy_a2a_no_selection_cannot_relax_strict_route(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """Legacy A2A optional selection cannot become strict-route fallback.

    The strict route uses a chat-less allowlist so its own decline stays a
    502; the point is that the legacy A2A ``None`` selection is fully
    isolated from the strict path and never relaxes it into a dispatch.
    """
    legacy_calls: list[str] = []

    async def legacy_no_selection(text: str) -> None:
        legacy_calls.append(text)
        return None

    monkeypatch.setattr(a2a_messages, "select_agent_tool", legacy_no_selection)
    assert await a2a_messages.select_agent_tool("legacy question") is None

    captured: dict[str, Any] = {}

    async def strict_no_selection(
        *,
        messages: list[dict[str, Any]],
        request: Any,
        completion: Any,
    ) -> SimpleNamespace:
        _ = messages, completion
        captured.update(
            {
                "tool_choice": request.tool_choice,
                "allowed_order": request.allowed_order,
            }
        )
        return _router_completion(empty_choices=True)

    monkeypatch.setattr(expert_router, "_run_completion", strict_no_selection)
    invoked = 0

    async def forbidden_invoke(
        **_kwargs: object,
    ) -> tuple[dict[str, Any], int]:
        nonlocal invoked
        invoked += 1
        raise AssertionError("strict route must stop before dispatch")

    monkeypatch.setattr(api_app, "_invoke_agent_run", forbidden_invoke)
    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "strict question",
            "allowed_tools": ["DataAgent", "KnowledgeAgent"],
        },
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == ("routing_contract_violation")
    assert captured == {
        "tool_choice": "auto",
        "allowed_order": ("DataAgent", "KnowledgeAgent"),
    }
    assert legacy_calls == ["legacy question"]
    assert invoked == 0
    assert not RunRegistry(tasks_db_path).list_runs(owner="u1")


async def test_route_unknown_tool_returns_502(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool outside the agent set (e.g. GetTaskStatus) -> 502."""
    invoked = 0

    async def forbidden_invoke(
        **_kwargs: object,
    ) -> tuple[dict[str, Any], int]:
        nonlocal invoked
        invoked += 1
        raise AssertionError("agent invocation must not run")

    monkeypatch.setattr(api_app, "_invoke_agent_run", forbidden_invoke)
    _patch_select(
        monkeypatch, ToolSelection("GetTaskStatus", {"task_id": "T-1"})
    )
    response = await _post_query_route(
        api_client,
        issued_api_key,
        {"user_query": "status?", "allowed_tools": ["ChatAgent"]},
    )
    assert response.status_code == 502
    assert invoked == 0
