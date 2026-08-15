# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Table-driven ownership checks for outbound service families."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import is_dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.responses import Response
from fastapi.routing import APIRoute, APIRouter
from pydantic import SecretStr
from starlette.requests import Request

from mcp_server_phytomni.api.auth import ApiPrincipal
from mcp_server_phytomni.api.relay import routes as relay_routes
from mcp_server_phytomni.api.relay.routes import (
    _ANALYSIS_LIFECYCLE,
    _OPENAI_RELAYS,
    _PLATFORM_RELAYS,
)
from mcp_server_phytomni.common.relay_client import (
    RelayClient,
    RelayRequestOptions,
)
from mcp_server_phytomni.runtime.outbound import OutboundPoolName
from mcp_server_phytomni.storage.research_objects import (
    ResearchObjectAuthority,
    ResearchObjectCandidate,
    ResearchObjectResolveRequest,
    ResearchObjectRevokeRequest,
    ResearchObjectSnapshot,
    ResearchObjectVerifyRequest,
    research_object_snapshot_payload,
)
from tests.support.outbound_fakes import ControlledByteStream
from tests.support.outbound_pool_contracts import (
    OPERATOR_FORWARD_ROUTES,
    OPERATOR_SERVER_TERMINATED_ROUTES,
    RELAY_CLIENT_WRAPPERS,
    OperatorForwardRoute,
    RelayClientWrapper,
)
from tests.support.relay_request import relay_request_scope

pytestmark = pytest.mark.agent

_SOURCE_ROOT = Path(__file__).parents[2] / "src" / "mcp_server_phytomni"


@pytest.mark.parametrize(
    ("relative_path", "pool"),
    [
        ("auth/iam.py", OutboundPoolName.IAM),
        ("agents/chat/service.py", OutboundPoolName.LLM),
        ("agents/expert/router.py", OutboundPoolName.LLM),
        ("agents/knowledge/retrieval.py", OutboundPoolName.RETRIEVAL),
        ("agents/knowledge/retrieval.py", OutboundPoolName.RERANK),
        ("agents/evolution/agent.py", OutboundPoolName.SPA_FAQ),
        ("agents/data/nl2sql.py", OutboundPoolName.NL2SQL),
        ("agents/analyst/graph.py", OutboundPoolName.ANALYSIS_CONTROL),
        ("agents/analyst/task_ops.py", OutboundPoolName.ANALYSIS_CONTROL),
        ("agents/analyst/task_ops.py", OutboundPoolName.ANALYSIS_STATUS),
        ("agents/shared/sql.py", OutboundPoolName.BI),
        ("common/relay_client.py", OutboundPoolName.OBS),
    ],
)
def test_direct_service_file_declares_its_typed_pool(
    relative_path: str,
    pool: OutboundPoolName,
) -> None:
    """Every Wave 1/2 adapter names its final typed logical pool."""
    source = (_SOURCE_ROOT / relative_path).read_text(encoding="utf-8")
    assert f"OutboundPoolName.{pool.name}" in source


def test_operator_route_declarations_are_frozen_typed_specs() -> None:
    """Every table entry exposes named route and pool fields."""
    for specs in (
        _OPENAI_RELAYS,
        _PLATFORM_RELAYS,
        _ANALYSIS_LIFECYCLE,
    ):
        for spec in specs:
            assert is_dataclass(spec)
            params = getattr(type(spec), "__dataclass_params__")
            assert params.frozen is True
            assert "__dict__" not in getattr(type(spec), "__slots__")
            assert spec.path
            assert spec.method in {"GET", "POST"}
            assert isinstance(spec.pool, OutboundPoolName)


@pytest.fixture(name="operator_pool_capture")
def _operator_pool_capture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> list[Any]:
    """Replace only the forward seam and record resolved upstream specs."""
    captured: list[Any] = []

    async def capture_forward(**kwargs: Any) -> Response:
        captured.append(kwargs["upstream"])
        return Response(status_code=200)

    async def fixed_body(request: Request, _max_bytes: int) -> bytes:
        if request.url.path == "/v1/relay/analysis/tasks":
            return b'{"compute_resource":"small"}'
        return b"{}"

    monkeypatch.setattr(relay_routes, "forward_relay_request", capture_forward)
    monkeypatch.setattr(relay_routes, "read_relay_body", fixed_body)
    monkeypatch.setattr(
        relay_routes, "get_audit_store", lambda _path: object()
    )
    monkeypatch.setattr(
        relay_routes,
        "ApiConfig",
        lambda: SimpleNamespace(
            RELAY_REQUEST_MAX_BYTES=1024,
            RELAY_AUDIT_DB_PATH=str(tmp_path / "unused.sqlite"),
        ),
    )
    monkeypatch.setattr(
        relay_routes,
        "DeepGenomeConfig",
        lambda: SimpleNamespace(
            RETRIEVE_URL="https://retrieve.test/search",
            RERANK_URL="https://rerank.test/rank",
            DATABASE_URL="https://database.test/nl2sql",
            ANALYSIS_URL="https://analysis.test/tasks",
            ANALYSIS_REGION="test-region",
            APP_ID={"small": "operator-small-app"},
            SPA_FAQ_URL="https://spa.test/{repo_id}",
        ),
    )
    monkeypatch.setattr(
        relay_routes,
        "get_sensitive_config",
        lambda: SimpleNamespace(
            BASE_URL="https://llm.test/v1",
            API_KEY=SecretStr("llm-key"),
            MODEL_ID="operator-llm-model",
            CODER_URL="https://coder.test/v1",
            CODER_API_KEY=SecretStr("coder-key"),
            CODER_MODEL="operator-coder-model",
            EMBED_URL="https://embed.test/v1",
            EMBED_API_KEY=SecretStr("embed-key"),
            EMBED_MODEL="operator-embed-model",
        ),
    )
    return captured


def _registered_endpoint(
    router: APIRouter, path: str, method: str
) -> Callable[..., Awaitable[Response]]:
    """Return one exact registered route endpoint."""
    return next(
        route.endpoint
        for route in router.routes
        if isinstance(route, APIRoute)
        and route.path == path
        and route.methods is not None
        and method in route.methods
    )


def _calls_forward_relay_request(endpoint: Callable[..., Any]) -> bool:
    """Return whether a registered endpoint reaches the HTTP forward seam."""
    pending = [endpoint]
    visited: set[Callable[..., Any]] = set()
    while pending:
        function = pending.pop()
        if function in visited:
            continue
        visited.add(function)
        names = set(function.__code__.co_names)
        if "forward_relay_request" in names:
            return True
        for name in names:
            dependency = function.__globals__.get(name)
            if inspect.isfunction(
                dependency
            ) and dependency.__module__.startswith(
                "mcp_server_phytomni.api.relay"
            ):
                pending.append(dependency)
    return False


def _partition_registered_routes(
    router: APIRouter,
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """Partition every registered API route by relay ownership."""
    forwarded: set[tuple[str, str]] = set()
    terminated: set[tuple[str, str]] = set()
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        assert (
            route.methods is not None
        ), f"registered route {route.path!r} has no HTTP methods"
        target = (
            forwarded
            if _calls_forward_relay_request(route.endpoint)
            else terminated
        )
        target.update((route.path, method) for method in route.methods)
    return forwarded, terminated


def test_operator_inventory_partitions_every_registered_route() -> None:
    """Forwarded and server-terminated inventories cover the real router."""
    router = relay_routes.create_relay_router()
    forwarded, terminated = _partition_registered_routes(router)

    assert forwarded == {
        (case.path, case.method) for case in OPERATOR_FORWARD_ROUTES
    }
    assert terminated == {
        (case.path, case.method) for case in OPERATOR_SERVER_TERMINATED_ROUTES
    }


def test_operator_inventory_rejects_route_without_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route inventory fails closed when FastAPI omits method metadata."""
    router = relay_routes.create_relay_router()
    route = next(
        route for route in router.routes if isinstance(route, APIRoute)
    )
    monkeypatch.setattr(route, "methods", None)

    with pytest.raises(AssertionError, match="has no HTTP methods"):
        _partition_registered_routes(router)


def test_relay_client_wrapper_inventory_is_complete() -> None:
    """Every public typed network wrapper has one runtime contract case."""
    network_calls = {
        "get_json",
        "post_json",
        "_request_json",
        "_request_bytes",
        "stream",
    }
    actual = {
        name
        for name, method in inspect.getmembers(
            RelayClient, inspect.iscoroutinefunction
        )
        if not name.startswith("_")
        and name not in {"get_json", "post_json"}
        and network_calls.intersection(method.__code__.co_names)
    }
    assert actual == {case.method for case in RELAY_CLIENT_WRAPPERS}


def _relay_client() -> RelayClient:
    """Return a fixed client for real wrapper-to-runtime classification."""
    return RelayClient(
        base_url="https://relay.test",
        api_key=SecretStr("recording-key"),
        timeout=1.0,
        max_retries=0,
        retriable_codes=(),
    )


def _research_snapshot() -> ResearchObjectSnapshot:
    """Return one valid immutable snapshot for grant wrapper calls."""
    return ResearchObjectSnapshot(
        dataset_id="dataset-1",
        size_bytes=17,
        etag="etag-1",
        version_id="version-1",
        last_modified="2026-08-14T00:00:00+00:00",
        placeholder=False,
        snapshot_digest="digest-1",
    )


def _grant_response(snapshot: ResearchObjectSnapshot) -> dict[str, Any]:
    """Return one valid grant response for resolve and verify wrappers."""
    return {
        "grants": [
            {
                "dataset_id": snapshot.dataset_id,
                "grant_id": "grant-1",
                "snapshot": research_object_snapshot_payload(snapshot),
                "expires_at": "2099-01-01T00:00:00+00:00",
                "revision": 1,
            }
        ]
    }


def _enqueue_wrapper_response(
    outbound_runtime: Any,
    case: RelayClientWrapper,
    snapshot: ResearchObjectSnapshot,
) -> None:
    """Queue the smallest valid response for one typed wrapper."""
    if case.method == "get_research_capabilities":
        payload: object = {
            "protocols": {"research_object_grant_v1": [1]},
            "research_object_grant": {"max_objects": 256},
        }
    elif case.method in {
        "resolve_research_objects",
        "verify_research_objects",
    }:
        payload = _grant_response(snapshot)
    elif case.method == "revoke_research_objects":
        payload = {"revoked": 1}
    elif case.method == "get_obs_object":
        outbound_runtime.transport.enqueue(content=b"object-bytes")
        return
    elif case.method == "get_obs_object_to_path":
        outbound_runtime.transport.enqueue(
            stream=ControlledByteStream(b"object", b"-bytes")
        )
        return
    elif case.method == "get_obs_list":
        payload = {"keys": ["result/file.txt"]}
    else:
        payload = {"obs_path": "/obs/phytomni/result"}
    outbound_runtime.transport.enqueue(
        content=json.dumps(payload).encode("utf-8")
    )


async def _invoke_wrapper(
    client: RelayClient,
    case: RelayClientWrapper,
    snapshot: ResearchObjectSnapshot,
    tmp_path: Path,
) -> None:
    """Invoke one public wrapper through its real production method."""
    if case.method == "get_research_capabilities":
        await client.get_research_capabilities()
    elif case.method == "resolve_research_objects":
        await client.resolve_research_objects(
            ResearchObjectResolveRequest(
                parent_run_id="run-1",
                execution_fingerprint="execution-1",
                objects=(
                    ResearchObjectCandidate(
                        snapshot.dataset_id,
                        "obs://bucket/result.vcf",
                        ".vcf",
                    ),
                ),
            )
        )
    elif case.method == "verify_research_objects":
        await client.verify_research_objects(
            ResearchObjectVerifyRequest(
                parent_run_id="run-1",
                execution_fingerprint="execution-1",
                authorities=(
                    ResearchObjectAuthority(
                        snapshot.dataset_id, "grant-0", snapshot
                    ),
                ),
            )
        )
    elif case.method == "revoke_research_objects":
        await client.revoke_research_objects(
            ResearchObjectRevokeRequest(
                parent_run_id="run-1",
                execution_fingerprint="execution-1",
                authority_ids=("grant-1",),
            )
        )
    elif case.method == "put_obs_object":
        await client.put_obs_object(
            "/obs/phytomni/result.txt",
            b"object-bytes",
            message="safe relay error",
        )
    elif case.method == "get_obs_object":
        await client.get_obs_object(
            "/obs/phytomni/result.txt", message="safe relay error"
        )
    elif case.method == "get_obs_object_to_path":
        await client.get_obs_object_to_path(
            "/obs/phytomni/result.txt",
            tmp_path / "result.txt",
            message="safe relay error",
        )
    elif case.method == "get_obs_list":
        await client.get_obs_list("result/", message="safe relay error")
    elif case.method == "put_obs_dir":
        await client.put_obs_dir("result/", message="safe relay error")
    else:
        raise AssertionError(f"missing wrapper driver: {case.method}")


@pytest.mark.parametrize("case", RELAY_CLIENT_WRAPPERS)
async def test_relay_client_wrapper_records_its_declared_pool(
    outbound_runtime: Any,
    tmp_path: Path,
    case: RelayClientWrapper,
) -> None:
    """Every production wrapper records one attempt in its owned pool."""
    snapshot = _research_snapshot()
    _enqueue_wrapper_response(outbound_runtime, case, snapshot)
    before = {
        name: outbound_runtime.runtime.pools.snapshot(name).started
        for name in OutboundPoolName
    }

    await _invoke_wrapper(_relay_client(), case, snapshot, tmp_path)

    after = {
        name: outbound_runtime.runtime.pools.snapshot(name).started
        for name in OutboundPoolName
    }
    assert {name: after[name] - before[name] for name in OutboundPoolName} == {
        name: int(name is case.pool) for name in OutboundPoolName
    }
    assert len(outbound_runtime.transport.requests) == 1
    request = outbound_runtime.transport.requests[0]
    assert request.method == case.http_method
    assert request.url.path == f"/v1/relay/{case.path}"


async def _drive_operator_route(
    router: APIRouter,
    path: str,
    method: str,
) -> None:
    """Invoke one registered forwarding handler with explicit dependencies."""
    concrete_path = path.replace("{task_id}", "task-marker").replace(
        "{repo_id}", "repo-marker"
    )
    scope = relay_request_scope()
    scope.update({"method": method, "path": concrete_path})
    kwargs: dict[str, Any] = {
        "request": Request(scope),
        "principal": ApiPrincipal(user_id="u1", key_prefix="ptm_test"),
    }
    if "{task_id}" in path:
        kwargs["task_id"] = "task-marker"
    if "{repo_id}" in path:
        kwargs["repo_id"] = "repo-marker"
    if path == "/v1/relay/analysis/tasks":
        kwargs["grant_store"] = object()
    await _registered_endpoint(router, path, method)(**kwargs)


@pytest.mark.parametrize("case", OPERATOR_FORWARD_ROUTES)
async def test_registered_operator_route_passes_its_final_pool_to_forward(
    operator_pool_capture: list[Any],
    case: OperatorForwardRoute,
) -> None:
    """The real registered handler passes its declared pool to forwarding."""
    await _drive_operator_route(
        relay_routes.create_relay_router(), case.path, case.method
    )
    assert len(operator_pool_capture) == 1
    assert operator_pool_capture[0].pool is case.pool


async def test_registered_operator_handler_does_not_ignore_spec_pool(
    monkeypatch: pytest.MonkeyPatch,
    operator_pool_capture: list[Any],
) -> None:
    """Forward an adversarial spec without reclassifying its pool."""
    adversarial = replace(_PLATFORM_RELAYS[0], pool=OutboundPoolName.BI)
    monkeypatch.setattr(
        "mcp_server_phytomni.api.relay.routes._OPENAI_RELAYS", ()
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.api.relay.routes._PLATFORM_RELAYS", (adversarial,)
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.api.relay.routes._ANALYSIS_LIFECYCLE", ()
    )

    router = relay_routes.create_relay_router()
    await _drive_operator_route(router, "/v1/relay/retrieve/search", "POST")
    assert len(operator_pool_capture) == 1
    assert operator_pool_capture[0].pool is OutboundPoolName.BI


async def test_relay_child_uses_explicit_pool_when_path_suggests_another(
    outbound_runtime: Any,
) -> None:
    """A relay path cannot override the caller's typed pool selection."""
    outbound_runtime.transport.enqueue(content=b"{}")
    client = RelayClient(
        base_url="https://relay.test",
        api_key=SecretStr("recording-key"),
        timeout=1.0,
        max_retries=0,
        retriable_codes=(),
    )

    await client.get_json(
        "analysis/task-marker",
        pool=OutboundPoolName.RERANK,
        options=RelayRequestOptions(
            message="adversarial relay request failed"
        ),
    )

    assert (
        outbound_runtime.runtime.pools.snapshot(
            OutboundPoolName.RERANK
        ).started
        == 1
    )
    assert (
        outbound_runtime.runtime.pools.snapshot(
            OutboundPoolName.ANALYSIS_STATUS
        ).started
        == 0
    )
