# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""HTTP Research input admission tests.

These tests keep the HTTP-only coordinator boundary separate from the MCP
schema.  The route adapter must perform an identity-only replay lookup before
parsing or resolving any user input.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from mcp_server_phytomni.agents.research import (
    dispatch_runtime as research_dispatch_runtime,
)
from mcp_server_phytomni.agents.research.input_contracts import (
    research_input_failure,
)
from mcp_server_phytomni.agents.research.input_inventory import (
    ManagedResearchAssetSnapshot,
)
from mcp_server_phytomni.agents.research.input_parser import (
    parse_research_input,
)
from mcp_server_phytomni.api import agent_runs as agent_runs_module
from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.api import app_support as app_support_module
from mcp_server_phytomni.api import research_capabilities
from mcp_server_phytomni.api import research_input as research_input_module
from mcp_server_phytomni.api.agent_runs import ResearchHttpRuntimeOptions
from mcp_server_phytomni.api.lifecycle_contract import (
    SafeApiError,
    project_research_lifecycle,
)
from mcp_server_phytomni.api.research_capabilities import (
    ResearchRelayCapabilities,
)
from mcp_server_phytomni.api.research_input import (
    ResearchAdmissionOutcome,
    ResearchCoordinatorRequest,
    ResearchHttpAdmissionInput,
    ResearchRoutePreflight,
    _ResearchInputRuntime,
    _run_production_coordinator_root,
    launch_research_input_worker,
    research_input_root_worker_ready,
)
from mcp_server_phytomni.api.resumable_uploads import UploadContractError
from mcp_server_phytomni.config.api_limits import ApiLimitsConfig
from mcp_server_phytomni.runtime.attachment_assets import (
    ResolvedAsset,
    ResolvedAttachmentBundle,
)
from mcp_server_phytomni.runtime.research_input_store import ResearchInputStore
from mcp_server_phytomni.runtime.run_registry import RunRegistry, RunSpec
from mcp_server_phytomni.storage.research_objects import (
    RelayResearchObjectMetadataPort,
)

pytestmark = pytest.mark.server


def _store(tmp_path: Path) -> Any:
    """Create the public tables before the Research private schema."""
    database = str(tmp_path / "research-http.sqlite")
    RunRegistry(database).create_run(
        RunSpec(
            run_id="unrelated",
            user_id="owner-1",
            agent="research",
            origin="api",
        )
    )
    return ResearchInputStore(database)


def _request(
    *,
    query: str = "summarize the inputs",
    key: str | None = "http-key",
    managed_asset_ids: tuple[str, ...] = ("asset-1",),
) -> ResearchHttpAdmissionInput:
    """Build one direct native HTTP request."""
    return ResearchHttpAdmissionInput(
        owner="owner-1",
        idempotency_key=key,
        conversation=None,
        original_query=query,
        managed_asset_ids=managed_asset_ids,
        locale="en-US",
        interop_mode="off",
        interop_targets=(),
        route_source="native",
    )


async def _assert_launch_failure(
    preflight: ResearchRoutePreflight,
    store: Any,
) -> str:
    """Assert that a failed root launch settles every durable parent row."""
    with pytest.raises(ValueError) as caught:
        await preflight.admit(_request())

    assert getattr(caught.value, "code", None) == (
        "research_input_resolution_unavailable"
    )
    assert getattr(caught.value, "http_status_hint", None) == 503
    assert getattr(caught.value, "retryable", None) is True
    with sqlite3.connect(store.db_path) as connection:
        run = connection.execute(
            "SELECT run_id, status, error, failure_json, expires_at, revision "
            "FROM runs "
            "WHERE agent = 'research' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert run is not None
        resolution = connection.execute(
            "SELECT status, failure_code, failure_retryable "
            "FROM research_input_resolutions WHERE run_id = ?",
            (run[0],),
        ).fetchone()
        root = connection.execute(
            "SELECT state, failure_code, failure_retryable "
            "FROM research_work_units WHERE run_id = ? "
            "AND kind = 'resolve_root'",
            (run[0],),
        ).fetchone()
    assert run[1:3] == ("failed", "research_input_resolution_unavailable")
    assert json.loads(run[3]) == {
        "code": "research_input_resolution_unavailable",
        "http_status_hint": 503,
        "retryable": True,
        "stage": "input_resolution",
    }
    assert run[4] is not None
    assert run[5] == 1
    assert resolution == (
        "failed",
        "research_input_resolution_unavailable",
        1,
    )
    assert root == (
        "retryable_failed",
        "research_input_resolution_unavailable",
        1,
    )
    return cast(str, run[0])


async def test_replay_is_checked_before_parser_or_managed_resolution(
    tmp_path: Path,
) -> None:
    """Exact replays do not repeat parser, resolver, or worker side effects."""
    store = _store(tmp_path)
    calls = SimpleNamespace(parse=0, resolve=0, launch=0)

    def parse(query: str, bucket: str) -> Any:
        calls.parse += 1
        return parse_research_input(query, bucket)

    def resolve(asset_ids: tuple[str, ...]) -> tuple[Any, ...]:
        calls.resolve += 1
        assert asset_ids == ("asset-1",)
        return (
            ManagedResearchAssetSnapshot(
                asset_id="asset-1",
                exact_reference="opaque-asset-1",
                size_bytes=1,
                purpose="dataset",
                completed=True,
                state_version=1,
                completed_at="2026-08-09T00:00:00+00:00",
                etag=None,
                version_id=None,
                last_modified=None,
                snapshot_digest="snapshot-1",
            ),
        )

    def launch(_request: ResearchHttpAdmissionInput, _outcome: Any) -> None:
        calls.launch += 1

    preflight = ResearchRoutePreflight(
        store=store,
        parser=parse,
        managed_snapshot_resolver=resolve,
        worker_launcher=launch,
    )

    first = await preflight.admit(_request())
    replay = await preflight.admit(_request())

    assert first.run_id == replay.run_id
    assert first.replay is False
    assert replay.replay is True
    assert calls.parse == 1
    assert calls.resolve == 1
    assert calls.launch == 1


async def test_false_worker_launch_fails_closed_after_admission(
    tmp_path: Path,
) -> None:
    """A failed production root launch cannot be reported as accepted work."""

    def resolve(
        _asset_ids: tuple[str, ...],
    ) -> tuple[ManagedResearchAssetSnapshot, ...]:
        return (
            ManagedResearchAssetSnapshot(
                asset_id="asset-1",
                exact_reference="opaque-asset-1",
                size_bytes=1,
                purpose="dataset",
                completed=True,
                state_version=1,
                completed_at="2026-08-09T00:00:00+00:00",
                etag=None,
                version_id=None,
                last_modified=None,
                snapshot_digest="snapshot-1",
            ),
        )

    launches: list[str] = []

    async def launch_false(*_args: Any) -> bool:
        launches.append("false")
        return False

    store = _store(tmp_path)
    preflight = ResearchRoutePreflight(
        store=store,
        managed_snapshot_resolver=resolve,
        runtime_ready=lambda: True,
        worker_launcher=launch_false,
    )

    run_id = await _assert_launch_failure(preflight, store)

    async def reject_inventory(*_args: Any) -> None:
        raise RuntimeError("validation unavailable")

    invalid_retry = ResearchRoutePreflight(
        store=store,
        managed_snapshot_resolver=resolve,
        runtime_ready=lambda: True,
        inventory_validator=reject_inventory,
    )
    with pytest.raises(ValueError):
        await invalid_retry.admit(_request())
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            "SELECT status FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone() == ("failed",)

    async def launch_true(*_args: Any) -> bool:
        launches.append("true")
        return True

    retry_preflight = ResearchRoutePreflight(
        store=store,
        managed_snapshot_resolver=resolve,
        runtime_ready=lambda: True,
        worker_launcher=launch_true,
    )
    retried = await retry_preflight.admit(_request())

    assert retried.replay is False
    assert retried.worker_owner is True
    assert launches == ["false", "true"]
    with sqlite3.connect(store.db_path) as connection:
        retried_row = connection.execute(
            "SELECT status, error, stage, failure_json, expires_at, revision "
            "FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    assert retried_row == ("running", None, "input_resolution", None, None, 2)
    assert RunRegistry(store.db_path).purge_expired() == 0
    assert (
        RunRegistry(store.db_path).get_run(run_id, owner="owner-1") is not None
    )


async def test_nonretryable_worker_failure_is_replayed_as_terminal(
    tmp_path: Path,
) -> None:
    """A classified root failure keeps its terminal public contract."""
    launches: list[str] = []

    async def reject(*_args: Any) -> bool:
        launches.append("reject")
        raise research_input_failure(
            "research_input_resolution_failed",
            "The coordinator rejected this request.",
            http_status_hint=422,
            retryable=False,
        )

    store = _store(tmp_path)
    preflight = ResearchRoutePreflight(
        store=store,
        runtime_ready=lambda: True,
        worker_launcher=reject,
    )
    with pytest.raises(ValueError) as caught:
        await preflight.admit(_request(managed_asset_ids=()))

    assert (
        getattr(caught.value, "code", None)
        == "research_input_resolution_failed"
    )
    assert getattr(caught.value, "http_status_hint", None) == 422
    assert getattr(caught.value, "retryable", None) is False
    with sqlite3.connect(store.db_path) as connection:
        row = connection.execute(
            "SELECT run_id, status, error, failure_json, expires_at, revision "
            "FROM runs WHERE agent = 'research' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        root = connection.execute(
            "SELECT state, failure_retryable, lease_expires_at "
            "FROM research_work_units WHERE run_id = ? "
            "AND kind = 'resolve_root'",
            (row[0],),
        ).fetchone()
        binding = connection.execute(
            "SELECT idempotency_digest, client_fingerprint "
            "FROM research_idempotency_bindings WHERE run_id = ?",
            (row[0],),
        ).fetchone()
    assert row[1:3] == ("failed", "research_input_resolution_failed")
    assert json.loads(row[3]) == {
        "code": "research_input_resolution_failed",
        "http_status_hint": 422,
        "retryable": False,
        "stage": "input_resolution",
    }
    assert row[4] is not None
    assert row[5] == 1
    assert root == ("terminal_failed", 0, None)
    assert binding is not None
    assert not store.retry_admission_available(
        row[0], "owner-1", binding[0], binding[1]
    )
    _, failure = project_research_lifecycle("failed", None, json.loads(row[3]))
    assert failure == {
        "code": "research_input_resolution_failed",
        "http_status_hint": 422,
        "message": "Research request could not be completed.",
        "retryable": False,
        "stage": "input_resolution",
    }

    replay = await preflight.admit(_request(managed_asset_ids=()))

    assert replay.replay is True
    assert replay.worker_owner is False
    assert replay.status_code == 200
    assert launches == ["reject"]


async def test_nonconversation_http_request_requires_idempotency_key(
    tmp_path: Path,
) -> None:
    """A direct HTTP call cannot reserve a run without its key."""
    preflight = ResearchRoutePreflight(store=_store(tmp_path))

    with pytest.raises(ValueError) as caught:
        await preflight.admit(_request(key=None))

    assert getattr(caught.value, "code", None) == (
        "research_idempotency_key_required"
    )


def test_research_http_admission_input_is_opaque_and_typed() -> None:
    """The adapter contract carries IDs/options, never projected paths."""
    request = _request()
    assert request.managed_asset_ids == ("asset-1",)
    assert not hasattr(request, "data_list")
    assert request.route_source == "native"


async def test_native_research_skips_generic_background_reservation(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Research admission owns its root row.

    It never reserves a generic run.
    """

    def forbidden_reservation(**_kwargs: Any) -> Any:
        raise AssertionError("generic background reservation was called")

    monkeypatch.setattr(
        api_app_module,
        "reserve_background_submission",
        forbidden_reservation,
    )
    headers = {
        "Authorization": f"Bearer {issued_api_key}",
        "Idempotency-Key": "native-research-1",
    }

    first = await api_client.post(
        "/v1/agents/research/runs",
        headers=headers,
        json={"arguments": {"user_query": "summarize inputs"}},
    )
    replay = await api_client.post(
        "/v1/agents/research/runs",
        headers=headers,
        json={"arguments": {"user_query": "summarize inputs"}},
    )

    assert first.status_code == 202, first.text
    assert replay.status_code == 202, replay.text
    assert first.json()["run_id"] == replay.json()["run_id"]
    assert first.json()["status"] == "running"


async def test_nonempty_native_research_data_block_has_no_resolution_io(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy projected inputs fail before attachment resolution or storage."""

    def forbidden_resolution(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("attachment resolution was called")

    monkeypatch.setattr(
        "mcp_server_phytomni.api.routes.agents.resolve_attachment_input",
        forbidden_resolution,
    )
    response = await api_client.post(
        "/v1/agents/research/runs",
        headers={
            "Authorization": f"Bearer {issued_api_key}",
            "Idempotency-Key": "invalid-research-block",
        },
        json={
            "arguments": {
                "user_query": "summarize inputs",
                "data_list": {"obs://private/path.csv": ""},
            }
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "research_data_block_invalid"


def _resolved_research_bundle() -> ResolvedAttachmentBundle:
    """Build one opaque managed asset for production admission seams."""
    return ResolvedAttachmentBundle(
        assets=(
            ResolvedAsset(
                asset_id="asset-1",
                reference="obs://private/asset-1.csv",
                filename="asset-1.csv",
                content_type="text/csv",
                size_bytes=1,
                purpose="dataset",
                state_version=1,
                completed_at="2026-08-09T00:00:00+00:00",
            ),
        )
    )


async def test_production_inventory_uses_active_relay_metadata_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Relay admission must not construct or use the direct OBS port."""
    relay_client = object()
    captured: dict[str, Any] = {}

    async def capture_inventory(request: Any, port: Any) -> None:
        captured["request"] = request
        captured["port"] = port

    monkeypatch.setattr(
        research_dispatch_runtime, "relay_mode_enabled", lambda: True
    )
    monkeypatch.setattr(
        research_dispatch_runtime,
        "current_relay_client",
        lambda: relay_client,
    )
    monkeypatch.setattr(
        research_dispatch_runtime,
        "current_outbound_runtime",
        lambda: (_ for _ in ()).throw(
            AssertionError("direct outbound runtime was accessed")
        ),
    )
    monkeypatch.setattr(
        agent_runs_module,
        "ServerConfig",
        lambda: SimpleNamespace(BUCKET_NAME="phytomni"),
    )
    monkeypatch.setattr(
        agent_runs_module,
        "validate_research_inventory",
        capture_inventory,
    )
    parsed = parse_research_input(
        'analyze\ndata: {"obs://phytomni/input.csv":""}',
        "phytomni",
    )

    validator = agent_runs_module._research_inventory_validator(
        cast(Any, ApiLimitsConfig())
    )
    await validator(parsed, ())

    assert isinstance(captured["port"], RelayResearchObjectMetadataPort)
    assert getattr(captured["port"], "_client") is relay_client
    assert captured["request"].configured_bucket == "phytomni"


async def test_production_replay_does_not_resolve_attachment_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exact HTTP replay skips the production attachment resolver."""
    database = _store(tmp_path).db_path
    bundle = _resolved_research_bundle()
    calls = 0

    def resolve_once() -> ResolvedAttachmentBundle:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AssertionError("attachment resolver ran during replay")
        return bundle

    monkeypatch.setattr(
        agent_runs_module,
        "research_input_runtime_capability",
        lambda *_args: SimpleNamespace(ready=True),
    )
    first = await agent_runs_module.invoke_research_http_run(
        _request(),
        resolve_once,
        config=cast(Any, ApiLimitsConfig()),
        db_path=database,
    )
    replay = await agent_runs_module.invoke_research_http_run(
        _request(),
        resolve_once,
        config=cast(Any, ApiLimitsConfig()),
        db_path=database,
    )

    assert first[1] == 202
    assert replay[1] == 202
    assert calls == 1


async def test_production_attachment_id_mismatch_fails_before_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolver output cannot replace the caller's opaque ID ordering."""
    monkeypatch.setattr(
        agent_runs_module,
        "research_input_runtime_capability",
        lambda *_args: SimpleNamespace(ready=True),
    )
    mismatched = ResolvedAttachmentBundle(
        assets=(
            ResolvedAsset(
                asset_id="asset-2",
                reference="obs://private/asset-2.csv",
                filename="asset-2.csv",
                content_type="text/csv",
                size_bytes=1,
                purpose="dataset",
                state_version=1,
                completed_at="2026-08-09T00:00:00+00:00",
            ),
        )
    )
    with pytest.raises(SafeApiError) as caught:
        await agent_runs_module.invoke_research_http_run(
            _request(),
            mismatched,
            config=cast(Any, ApiLimitsConfig()),
            db_path=str(tmp_path / "mismatch.sqlite"),
        )

    assert getattr(caught.value, "status_code", None) == 422
    assert getattr(caught.value, "code", None) == "invalid_upload_metadata"


async def test_production_upload_contract_error_preserves_http_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Owner resolver contract status is not collapsed to generic 400."""
    monkeypatch.setattr(
        agent_runs_module,
        "research_input_runtime_capability",
        lambda *_args: SimpleNamespace(ready=True),
    )

    def resolve() -> ResolvedAttachmentBundle:
        raise UploadContractError("upload_asset_not_found", 404)

    with pytest.raises(SafeApiError) as caught:
        await agent_runs_module.invoke_research_http_run(
            _request(),
            resolve,
            config=cast(Any, ApiLimitsConfig()),
            db_path=str(tmp_path / "upload-error.sqlite"),
        )

    assert getattr(caught.value, "status_code", None) == 404
    assert getattr(caught.value, "code", None) == "upload_asset_not_found"


async def test_fresh_production_admission_launches_coordinator_owner_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the fresh admission owner launches the Research coordinator."""
    database = _store(tmp_path).db_path
    launches: list[tuple[Any, Any]] = []

    async def launcher(
        request: ResearchHttpAdmissionInput,
        outcome: Any,
    ) -> None:
        launches.append((request, outcome))

    monkeypatch.setattr(
        agent_runs_module,
        "research_input_runtime_capability",
        lambda *_args: SimpleNamespace(ready=True),
    )
    monkeypatch.setattr(
        agent_runs_module,
        "launch_research_input_worker",
        launcher,
        raising=False,
    )
    bundle = _resolved_research_bundle()
    first = await agent_runs_module.invoke_research_http_run(
        _request(key="worker-owner"),
        bundle,
        config=cast(Any, ApiLimitsConfig()),
        db_path=database,
    )
    replay = await agent_runs_module.invoke_research_http_run(
        _request(key="worker-owner"),
        bundle,
        config=cast(Any, ApiLimitsConfig()),
        db_path=database,
    )

    assert first[1] == 202
    assert replay[1] == 202
    assert len(launches) == 1
    assert launches[0][0].idempotency_key == "worker-owner"
    assert launches[0][1].worker_owner is True


async def test_production_launcher_builds_a_fresh_root_from_request_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A root factory supplies one request without mutating shared runtime."""
    factory_calls: list[Any] = []
    run_calls: list[tuple[Any, str, str]] = []
    shared_request = object()

    def request_factory(admission: Any) -> ResearchCoordinatorRequest:
        factory_calls.append(admission)
        return ResearchCoordinatorRequest(
            "request-factory-placeholder", object()
        )

    async def run(
        self: Any,
        run_id: str,
        lease_owner: str,
    ) -> None:
        run_calls.append((self, run_id, lease_owner))

    monkeypatch.setattr(
        research_input_module.ResearchInputCoordinator, "run", run
    )
    coordinator = SimpleNamespace(
        outbox=object(),
        recovery=object(),
        request=shared_request,
    )
    runtime = _ResearchInputRuntime(
        coordinator,
        SimpleNamespace(),
        _run_production_coordinator_root,
        request_factory,
    )
    runtime_state = getattr(research_input_module, "_RUNTIME_STATE")
    original = runtime_state["current"]
    runtime_state["current"] = runtime
    admission = cast(Any, SimpleNamespace())
    try:
        await launch_research_input_worker(
            _request(),
            ResearchAdmissionOutcome(
                run_id="run-factory",
                replay=False,
                worker_owner=True,
                status_code=202,
            ),
            admission,
        )
    finally:
        runtime_state["current"] = original

    assert factory_calls == [admission]
    assert run_calls[0][0] is not coordinator
    assert run_calls[0][1] == "run-factory"
    assert run_calls[0][2].startswith("research-http-")
    assert coordinator.request is shared_request


async def test_root_launcher_does_not_use_recovery_scan() -> None:
    """A missing request factory cannot be papered over by a recovery scan."""
    recovery_calls: list[datetime] = []

    async def recover_once(now: datetime) -> None:
        recovery_calls.append(now)

    runtime = _ResearchInputRuntime(
        SimpleNamespace(outbox=object(), recovery=object()),
        SimpleNamespace(recover_once=recover_once),
        _run_production_coordinator_root,
    )
    runtime_state = getattr(research_input_module, "_RUNTIME_STATE")
    original = runtime_state["current"]
    runtime_state["current"] = runtime
    try:
        await launch_research_input_worker(
            _request(),
            ResearchAdmissionOutcome(
                run_id="run-unbound",
                replay=False,
                worker_owner=True,
                status_code=202,
            ),
            cast(Any, SimpleNamespace()),
        )
    finally:
        runtime_state["current"] = original

    assert not recovery_calls


def test_production_root_readiness_requires_a_request_factory() -> None:
    """Serving stays unavailable until coordinator input can be built."""
    runtime_state = getattr(research_input_module, "_RUNTIME_STATE")
    original = runtime_state["current"]
    runtime_state["current"] = _ResearchInputRuntime(
        SimpleNamespace(),
        SimpleNamespace(),
        _run_production_coordinator_root,
    )
    try:
        assert research_input_root_worker_ready() is False
        runtime_state["current"] = _ResearchInputRuntime(
            SimpleNamespace(),
            SimpleNamespace(),
            _run_production_coordinator_root,
            lambda _admission: ResearchCoordinatorRequest("run", object()),
        )
        assert research_input_root_worker_ready() is True
    finally:
        runtime_state["current"] = original


def test_uninstalled_runtime_is_not_production_ready() -> None:
    """Only direct adapter tests may opt into an uninstalled runtime seam."""
    runtime_state = getattr(research_input_module, "_RUNTIME_STATE")
    original = runtime_state["current"]
    runtime_state["current"] = None
    try:
        assert research_input_root_worker_ready() is False
        assert research_input_root_worker_ready(allow_uninstalled=True) is True
    finally:
        runtime_state["current"] = original


async def test_production_http_admission_requires_installed_root_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The serving adapter rejects fresh work before attachment resolution."""
    runtime_state = getattr(research_input_module, "_RUNTIME_STATE")
    original = runtime_state["current"]
    runtime_state["current"] = None
    monkeypatch.setattr(
        agent_runs_module,
        "research_input_runtime_capability",
        lambda *_args: SimpleNamespace(ready=True),
    )
    try:
        with pytest.raises(SafeApiError) as caught:
            await agent_runs_module.invoke_research_http_run(
                _request(),
                object(),
                config=cast(Any, ApiLimitsConfig()),
                db_path=str(tmp_path / "uninstalled-root.sqlite"),
                runtime_options=ResearchHttpRuntimeOptions(
                    allow_uninstalled=False
                ),
            )
    finally:
        runtime_state["current"] = original

    assert caught.value.status_code == 503
    assert caught.value.code == "research_input_protocol_unavailable"


def test_build_app_exposes_injected_research_root_factory() -> None:
    """The app lifespan can read the root factory supplied at construction."""

    def root_request_factory(_admission: Any) -> ResearchCoordinatorRequest:
        return ResearchCoordinatorRequest("run", object())

    app = api_app_module.create_app(
        research_input_root_request_factory=root_request_factory
    )

    assert (
        app.state.research_input_root_request_factory is root_request_factory
    )


async def test_catalog_omits_research_protocol_without_root_factory(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
) -> None:
    """The catalog does not advertise a runtime that cannot launch roots."""
    runtime_state = getattr(research_input_module, "_RUNTIME_STATE")
    original = runtime_state["current"]
    runtime_state["current"] = _ResearchInputRuntime(
        SimpleNamespace(),
        SimpleNamespace(),
        _run_production_coordinator_root,
    )
    try:
        response = await api_client.get(
            "/v1/agents",
            headers={"Authorization": f"Bearer {issued_api_key}"},
        )
    finally:
        runtime_state["current"] = original

    assert response.status_code == 200
    payload = response.json()
    assert "research_input_resolution_v1" not in payload["protocols"]
    assert "research_input_resolution" not in payload


async def test_relay_lifespan_refresh_feeds_ready_catalog_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup refresh is awaited and its fresh snapshot makes relay ready."""
    calls: list[Any] = []
    now = datetime.now(UTC)
    snapshot = ResearchRelayCapabilities(
        protocol_versions=(1,),
        max_objects=256,
        authorized_scope="relay:research-input",
        obtained_at=now,
        expires_at=now + timedelta(seconds=60),
    )

    async def refresh(config: Any) -> ResearchRelayCapabilities:
        calls.append(config)
        return snapshot

    monkeypatch.setattr(
        app_support_module,
        "refresh_research_relay_capability",
        refresh,
        raising=False,
    )
    monkeypatch.setattr(
        app_support_module, "validate_citation_database", _noop_sync
    )
    monkeypatch.setattr(
        app_support_module,
        "init_outbound_runtime",
        _completed_async_call,
    )
    monkeypatch.setattr(
        app_support_module, "ensure_research_input_runtime", _noop_sync
    )
    monkeypatch.setattr(
        app_support_module,
        "recover_registered_startup",
        _completed_async_call,
    )
    monkeypatch.setattr(
        app_support_module,
        "aclose_outbound_runtime",
        _completed_async_call,
    )
    monkeypatch.setattr(
        app_support_module,
        "aclose_gauss_pool",
        _completed_async_call,
    )

    lifespan = getattr(app_support_module, "_http_lifespan")
    async with lifespan(cast(Any, SimpleNamespace())):
        pass

    assert len(calls) == 1
    config = SimpleNamespace(
        RELAY_MODE=True,
        API_MAX_USER_QUERY_CHARS=131_072,
        API_MAX_ATTACHMENTS_PER_REQUEST=64,
        API_MAX_RESEARCH_DATASET_PATHS=64,
        API_MAX_RESEARCH_INPUT_REFERENCES=128,
    )
    capability = research_capabilities.research_input_runtime_capability(
        cast(Any, config), snapshot
    )
    assert capability.ready is True
    assert capability.descriptor is not None


async def test_relay_refresh_populates_the_serving_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful startup handshake is the snapshot serving consumes."""
    now = datetime.now(UTC)
    received = ResearchRelayCapabilities(
        protocol_versions=(1,),
        max_objects=256,
        authorized_scope="relay:research-input",
        obtained_at=now,
        expires_at=now + timedelta(seconds=60),
    )

    async def get_research_capabilities() -> ResearchRelayCapabilities:
        """Return one bounded capability snapshot."""
        return received

    def current_relay_client() -> Any:
        """Return the fake relay client for this cache test."""
        return SimpleNamespace(
            get_research_capabilities=get_research_capabilities
        )

    cache = research_capabilities.ResearchRelayCapabilityCache()
    monkeypatch.setattr(
        research_capabilities, "_RELAY_CAPABILITY_CACHE", cache
    )
    monkeypatch.setattr(
        research_capabilities,
        "_current_relay_client",
        current_relay_client,
    )
    config = SimpleNamespace(RELAY_MODE=True)

    result = await research_capabilities.refresh_research_relay_capability(
        cast(Any, config), now
    )
    snapshot = research_capabilities.current_research_relay_snapshot(
        cast(Any, config), now
    )

    assert result is not None
    assert snapshot == result
    assert snapshot is not None
    assert snapshot.protocol_versions == (1,)


def _noop_sync() -> None:
    """Provide a tiny synchronous seam for isolated lifespan tests."""


async def _completed_async_call() -> None:
    """Provide a tiny awaitable for isolated lifespan tests."""
