# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Private-data redaction invariants for Research public projections."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from types import MappingProxyType, SimpleNamespace
from typing import Any

import httpx
import pytest
from tests.support.sqlite import closed_sqlite_connection

from mcp_server_phytomni.agents.research import dispatch_runtime
from mcp_server_phytomni.agents.research.dispatch_outbox import (
    ResearchDispatchOutbox,
    persist_plan_and_outbox,
)
from mcp_server_phytomni.agents.research.dispatch_outbox_storage import (
    payload_is_consistent,
)
from mcp_server_phytomni.agents.research.input_preparation import (
    PreparedResearchInput,
)
from mcp_server_phytomni.agents.research.planning import (
    ResearchChildPlan,
    ResearchPlan,
)
from mcp_server_phytomni.api.auth import ApiPrincipal
from mcp_server_phytomni.api.lifecycle_contract import (
    empty_agent_result,
    project_research_lifecycle,
)
from mcp_server_phytomni.api.relay import (
    research_input as relay_research_input,
)
from mcp_server_phytomni.api.relay import routes as relay_routes
from mcp_server_phytomni.runtime.research_input_store import (
    ResearchInputStore,
)
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunRequestInfo,
    RunSpec,
)

pytestmark = pytest.mark.server

FORBIDDEN_MARKERS = (
    "obs://private-bucket/private/key.tsv",
    "PRIVATE_QUERY_MARKER",
    "PRIVATE_DOCUMENT_MARKER",
    "PRIVATE_PROMPT_MARKER",
    "PRIVATE_GRANT_MARKER",
    "PRIVATE_TOKEN_MARKER",
    "https://private-endpoint.invalid",
)


def assert_research_markers_absent(value: object) -> None:
    """Reject every known private marker from a public-safe boundary value."""
    rendered = json.dumps(value, ensure_ascii=False, default=str)
    for marker in FORBIDDEN_MARKERS:
        assert marker not in rendered


def _payload_digest(payload: object) -> str:
    """Return the outbox canonical digest used by the tamper fixture."""
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_nonblank_payload_digest_requires_bound_fields() -> None:
    """A recomputed digest cannot legitimize a payload missing bindings."""
    payload = {"goal_description": "safe"}
    digest = _payload_digest(payload)
    assert not payload_is_consistent(payload, digest, "child-output", "f" * 64)


def _private_failure() -> dict[str, Any]:
    """Seed every private channel while retaining one valid public failure."""
    return {
        "code": "research_input_resolution_unavailable",
        "message": "PRIVATE_QUERY_MARKER",
        "stage": "input_resolution",
        "retryable": True,
        "http_status_hint": 503,
        "query": "PRIVATE_QUERY_MARKER",
        "document": "PRIVATE_DOCUMENT_MARKER",
        "prompt": "PRIVATE_PROMPT_MARKER",
        "grant": "PRIVATE_GRANT_MARKER",
        "token": "PRIVATE_TOKEN_MARKER",
        "path": "obs://private-bucket/private/key.tsv",
        "endpoint": "https://private-endpoint.invalid",
    }


def test_failed_research_projection_never_exposes_private_markers() -> None:
    """A durable failure projects only its stable public contract fields."""
    stage, failure = project_research_lifecycle(
        "failed", "execution", _private_failure()
    )

    assert stage is None
    assert failure == {
        "code": "research_input_resolution_unavailable",
        "message": "Research request could not be completed.",
        "stage": "input_resolution",
        "retryable": True,
        "http_status_hint": 503,
    }
    assert_research_markers_absent({"stage": stage, "failure": failure})


@pytest.mark.parametrize("status", ("running", "succeeded"))
def test_nonterminal_or_success_projection_drops_private_failure_payload(
    status: str,
) -> None:
    """Only a validated failed state can expose bounded failure metadata."""
    stage, failure = project_research_lifecycle(
        status, "execution", _private_failure()
    )

    assert failure is None
    assert stage == ("execution" if status == "running" else None)
    assert_research_markers_absent({"stage": stage, "failure": failure})


def test_cancelled_research_projection_keeps_only_bounded_failure_fields() -> (
    None
):
    """Cancellation may retain an error, but never any private diagnostics."""
    stage, failure = project_research_lifecycle(
        "cancelled", "execution", _private_failure()
    )

    assert stage is None
    assert failure is not None
    assert failure["code"] == "research_input_resolution_unavailable"
    assert_research_markers_absent({"stage": stage, "failure": failure})


def test_relay_audit_and_warning_logs_never_retain_private_markers(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Research relay hashes private identifiers before audit or logging."""
    stored: list[object] = []
    audit_context_type = getattr(relay_research_input, "_AuditContext")
    record_audit = getattr(relay_research_input, "_record_audit")
    context = audit_context_type(
        principal=ApiPrincipal("safe-user", "safe-prefix"),
        operation="research_grant_resolve",
        started=time.monotonic(),
        dataset_ids=FORBIDDEN_MARKERS,
        grant_ids=FORBIDDEN_MARKERS,
    )
    monkeypatch.setattr(
        relay_research_input,
        "get_audit_store",
        lambda _path: SimpleNamespace(record=stored.append),
    )

    record_audit(context)

    assert len(stored) == 1
    assert_research_markers_absent(stored)

    def fail_record(_entry: object) -> None:
        raise OSError("PRIVATE_TOKEN_MARKER")

    monkeypatch.setattr(
        relay_research_input,
        "get_audit_store",
        lambda _path: SimpleNamespace(record=fail_record),
    )
    with caplog.at_level(
        logging.WARNING, logger=relay_research_input.__name__
    ):
        record_audit(context)

    assert_research_markers_absent(caplog.messages)


async def test_research_http_reads_never_project_private_request_metadata(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Default/debug detail, list, and refresh hide every private marker."""
    run_id = "run-redaction-private-request-metadata"
    dialogue_id = "dialogue-redaction-private-request-metadata"
    request_metadata = {
        "document": "PRIVATE_DOCUMENT_MARKER",
        "prompt": "PRIVATE_PROMPT_MARKER",
        "grant": "PRIVATE_GRANT_MARKER",
        "token": "PRIVATE_TOKEN_MARKER",
        "path": "obs://private-bucket/private/key.tsv",
        "endpoint": "https://private-endpoint.invalid",
    }
    registry = RunRegistry(tasks_db_path)
    registry.create_run(
        RunSpec(run_id, "u1", "research", "api"),
        outcome=RunOutcome(status="failed", result=empty_agent_result()),
        request_info=RunRequestInfo(
            dialogue_id=dialogue_id,
            query="PRIVATE_QUERY_MARKER",
            request_json=json.dumps(request_metadata),
        ),
    )
    with closed_sqlite_connection(tasks_db_path) as connection:
        connection.execute(
            "UPDATE runs SET stage = ?, failure_json = ? WHERE run_id = ?",
            (
                "input_resolution",
                json.dumps(
                    {
                        "code": "research_input_resolution_failed",
                        "message": "PRIVATE_QUERY_MARKER",
                        "stage": "input_resolution",
                        "retryable": False,
                        "http_status_hint": 422,
                        **request_metadata,
                    }
                ),
                run_id,
            ),
        )

    headers = {"Authorization": f"Bearer {issued_api_key}"}
    responses = (
        await api_client.get(f"/v1/runs/{run_id}", headers=headers),
        await api_client.get(
            f"/v1/runs?dialogue_id={dialogue_id}", headers=headers
        ),
        await api_client.get(f"/v1/runs/{run_id}", headers=headers),
        await api_client.get(f"/v1/runs/{run_id}?debug=true", headers=headers),
    )

    assert all(response.status_code == 200 for response in responses)
    assert_research_markers_absent([response.json() for response in responses])


async def test_dispatch_runtime_outbox_never_forwards_private_input_channels(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tampered durable child fails closed before Analyst submission."""
    database = str(tmp_path / "dispatch-redaction.sqlite")
    RunRegistry(database).create_run(
        RunSpec("run-private-dispatch", "owner", "research", "api")
    )
    store = ResearchInputStore(database)
    assert store.persist_resolution(
        "run-private-dispatch",
        original_query_digest="q" * 64,
        original_query_length=5,
        effective_query="safe query",
        source_map={"synthetic": "private"},
        parsed_candidates=[{"reference": "synthetic"}],
        managed_snapshot=[{"asset_id": "synthetic"}],
        evidence_digest="e" * 64,
        work_digest="w" * 64,
    )
    prepared = PreparedResearchInput(
        effective_query="safe query",
        obs_file_list=(),
        data_list=MappingProxyType({"safe.tsv": "safe dataset"}),
        inventory_digest="i" * 64,
        evidence_digest="e" * 64,
        execution_fingerprint="f" * 64,
        authority_ids=(),
    )
    plan = ResearchPlan(
        goals=(),
        children=(
            ResearchChildPlan(
                ordinal=0,
                task_name="safe-task",
                goal_description="safe goal",
                context="safe context",
                data_list=MappingProxyType({"safe.tsv": "safe dataset"}),
                output_dir="research/run-private-dispatch/children/part-001",
                thread_id="thread-private-dispatch",
                interop_mode="off",
                interop_targets=(),
                dispatch_fingerprint="d" * 64,
            ),
        ),
        digest="a" * 64,
    )
    record = persist_plan_and_outbox(
        store, "run-private-dispatch", 0, prepared, plan
    )[0]
    with closed_sqlite_connection(database) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM research_dispatch_outbox "
                "WHERE outbox_id = ?",
                (record.dispatch_id,),
            ).fetchone()[0]
        )
        payload.update(
            goal_description="PRIVATE_PROMPT_MARKER",
            context="PRIVATE_QUERY_MARKER",
            data_list={
                "obs://private-bucket/private/key.tsv": (
                    "PRIVATE_DOCUMENT_MARKER"
                )
            },
            research_grants=[
                {
                    "dataset_id": "safe-dataset",
                    "exact_reference": "https://private-endpoint.invalid",
                    "grant_id": "PRIVATE_GRANT_MARKER",
                    "snapshot_digest": "PRIVATE_TOKEN_MARKER",
                }
            ],
        )
        payload.pop("output_dir", None)
        payload.pop("dispatch_fingerprint", None)
        payload_json = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        connection.execute(
            "UPDATE research_dispatch_outbox SET payload_json = ?, "
            "payload_digest = ?, output_dir = ? WHERE outbox_id = ?",
            (
                payload_json,
                _payload_digest(payload),
                "research/PRIVATE_DOCUMENT_MARKER/children/part-001",
                record.dispatch_id,
            ),
        )
        connection.commit()

    captured: list[object] = []

    async def submit_remote_analysis(
        _agent: object,
        _config: object,
        _secrets: object,
        request: object,
        *,
        is_polling: bool,
    ) -> dict[str, str]:
        """Capture the real runtime request at the external Analyst seam."""
        assert is_polling is False
        captured.append(request)
        return {"task_id": "private-dispatch-task"}

    monkeypatch.setattr(
        dispatch_runtime, "submit_remote_analysis", submit_remote_analysis
    )
    bindings_type = getattr(dispatch_runtime, "_RuntimeBindings")
    bindings = bindings_type(
        analyst_agent=object(),
        analyst_config=SimpleNamespace(COMPUTE_RESOURCE="medium"),
        sensitive_config=object(),
        metadata_port=object(),
    )
    disposition = await ResearchDispatchOutbox(
        store,
        submit=bindings.submit,
        authority_verifier=lambda _record: True,
    ).dispatch_once(record.dispatch_id, "redaction-worker")

    assert disposition.state == "ambiguous"
    assert not captured


def test_operator_relay_strips_private_sidecar_before_upstream_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The analysis platform receives no private sidecar marker bytes."""
    private_sidecar = {
        "schema_version": 1,
        "parent_run_id": (
            "PRIVATE_QUERY_MARKER https://private-endpoint.invalid"
        ),
        "execution_fingerprint": "PRIVATE_PROMPT_MARKER",
        "objects": [
            {
                "dataset_id": "PRIVATE_DOCUMENT_MARKER",
                "exact_reference": "obs://private-bucket/private/key.tsv",
                "grant_id": "PRIVATE_GRANT_MARKER",
                "snapshot_digest": "PRIVATE_TOKEN_MARKER",
            }
        ],
    }
    monkeypatch.setattr(
        relay_routes,
        "_verify_research_sidecar",
        lambda _principal, _sidecar, _store: None,
    )

    unwrap = getattr(relay_routes, "_unwrap_research_analysis_body")
    upstream_body = unwrap(
        json.dumps(
            {
                "analysis_request": {"name": "safe-analysis", "tasks": []},
                "research_input_grants": private_sidecar,
            }
        ).encode("utf-8"),
        ApiPrincipal(
            "owner",
            "safe-prefix",
            frozenset({"relay:research-input"}),
        ),
        object(),
    )

    assert upstream_body == b'{"name":"safe-analysis","tasks":[]}'
    assert_research_markers_absent(upstream_body.decode("utf-8"))
