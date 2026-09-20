# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Transport entry adapters preserve business values behind one Runtime."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from tests.support.execution_dispatch_fixtures import (
    canonical_test_identity,
    reserve_test_execution,
)
from tests.support.execution_runtime_v2 import first_event_of_type

from mcp_server_phytomni.public_agent_catalog import public_agent_spec
from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
    CanonicalReservationIdentity,
    bind_canonical_reservation_identity,
    invoke_public_agent,
)
from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_journal_v2 import ExecutionEventType
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    ExecutionReservationConflictError,
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    ExecutionCommand,
    ExecutionRuntimeError,
)
from mcp_server_phytomni.runtime.execution_target_store_v2 import (
    SQLiteExecutionTargetStore,
)
from mcp_server_phytomni.runtime.sqlite import sqlite_transaction


def test_entrypoint_projects_downloadable_artifact_to_opaque_target(
    tmp_path: Path,
) -> None:
    """Existing report envelopes gain a stable private delivery binding."""

    db_path = str(tmp_path / "artifact-entrypoint.db")
    delivery_ref = "obs://phytomni/runs/run-1/report.md"

    async def business_call():
        return {
            "formatted": {"answer": "done"},
            "execution": {
                "artifacts": [
                    {
                        "role": "scientific_report",
                        "name": "report.md",
                        "media_type": "text/markdown",
                        "size_bytes": 12,
                        "downloadable": True,
                        "download_ref": delivery_ref,
                    }
                ]
            },
        }

    asyncio.run(
        invoke_public_agent(
            db_path=db_path,
            owner="alice",
            execution_id="turn-artifact-entrypoint",
            agent_slug="knowledge",
            arguments={"query": "rice"},
            transport="http",
            call=business_call,
        )
    )

    store = SQLiteExecutionTargetStore(db_path)
    with sqlite_transaction(db_path) as connection:
        target_id = connection.execute(
            "SELECT target_id FROM execution_target_bindings_v2 "
            "WHERE owner_ref = ? AND execution_id = ?",
            ("alice", "turn-artifact-entrypoint"),
        ).fetchone()[0]
    assert target_id.startswith("artifact-")
    binding = store.get(
        owner="alice",
        execution_id="turn-artifact-entrypoint",
        kind="artifact",
        target_id=target_id,
    )
    assert binding is not None
    assert binding.delivery_ref == delivery_ref


def test_network_ready_result_projects_report_table_image_and_archive_targets(
    tmp_path: Path,
) -> None:
    """A ready Network result keeps every deliverable behind opaque targets."""

    db_path = str(tmp_path / "network-results-entrypoint.db")
    digest = "sha256:" + "3" * 64
    run_root = "obs://phytomni/runs/run-network"
    inventory_ref = (
        f"{run_root}/delivery/{digest.removeprefix('sha256:')}/"
        ".phytomni-result-inventory.json"
    )
    artifacts = [
        {
            "role": "scientific_report",
            "name": "network-report.pdf",
            "media_type": "application/pdf",
            "size_bytes": 216_300,
            "download_ref": f"{run_root}/children/part-001/network-report.pdf",
        },
        {
            "role": "scientific_table",
            "name": "top20-genes.csv",
            "media_type": "text/csv",
            "size_bytes": 1_024,
            "download_ref": f"{run_root}/children/part-001/top20-genes.csv",
        },
        {
            "role": "scientific_image",
            "name": "module-network.png",
            "media_type": "image/png",
            "size_bytes": 7_200_000,
            "download_ref": f"{run_root}/children/part-001/module-network.png",
        },
    ]

    async def business_call():
        return {
            "status": "succeeded",
            "result": {
                "formatted": {"answer": "# Network report\n\nCompleted."},
                "execution": {
                    "artifacts": artifacts,
                    "delivery": {
                        "schema_version": 1,
                        "required": True,
                        "status": "ready",
                        "revision": 1,
                        "inventory_digest": digest,
                        "archive": {
                            "role": "result_archive",
                            "name": "network-results.zip",
                            "media_type": "application/zip",
                            "size_bytes": 224_100_000,
                            "downloadable": True,
                            "report_context_eligible": False,
                            "download_ref": f"result-archive:{digest}",
                        },
                        "error_code": None,
                        "retryable": False,
                    },
                },
                "delivery_internal": {
                    "inventory_ref": inventory_ref,
                    "attempts_claimed": 1,
                    "last_error_code": None,
                },
            },
        }

    asyncio.run(
        invoke_public_agent(
            db_path=db_path,
            owner="alice",
            execution_id="turn-network-results",
            agent_slug="network",
            arguments={"to_id": "TO:0000011", "species_code": "osa"},
            transport="supervisor",
            call=business_call,
        )
    )

    with sqlite_transaction(db_path) as connection:
        rows = connection.execute(
            "SELECT target_kind, target_id, role, name, delivery_ref "
            "FROM execution_target_bindings_v2 "
            "WHERE owner_ref = ? AND execution_id = ? ORDER BY name",
            ("alice", "turn-network-results"),
        ).fetchall()
    assert {row[2] for row in rows} == {
        "scientific_report",
        "scientific_table",
        "scientific_image",
        "result_archive",
    }
    archive = next(row for row in rows if row[2] == "result_archive")
    assert archive[0] == "download"
    assert archive[1].startswith("download-")
    assert archive[3] == "network-results.zip"
    assert archive[4] == (
        f"{run_root}/delivery/{digest.removeprefix('sha256:')}/"
        "network-results.zip"
    )

    projection = SQLiteExecutionJournal(db_path).get_projection(
        "turn-network-results", owner="alice"
    )
    assert {result.name for result in projection.results} == {
        "network-report.pdf",
        "top20-genes.csv",
        "module-network.png",
        "network-results.zip",
    }
    public_projection = projection.model_dump(mode="json")
    assert run_root not in repr(public_projection)
    assert inventory_ref not in repr(public_projection)


@pytest.mark.parametrize(
    "transport",
    [
        "authenticated_http",
        "expert_router",
        "openai",
        "mcp",
        "background",
        "resume",
        "recovery",
    ],
)
def test_transport_entry_preserves_business_value_and_uses_runtime(
    tmp_path: Path,
    transport: str,
) -> None:
    """Verify transport entry preserves business value and uses runtime."""

    calls = 0

    async def business_call():
        nonlocal calls
        calls += 1
        return {"unchanged": "business-result"}

    db_path = tmp_path / f"{transport}.db"
    value = asyncio.run(
        invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id=f"turn-{transport}",
            agent_slug="chat",
            arguments={"query": "rice"},
            transport=transport,
            call=business_call,
        )
    )

    assert value == {"unchanged": "business-result"}
    assert calls == 1
    page = SQLiteExecutionJournal(str(db_path)).list_events(
        f"turn-{transport}", owner="alice", limit=20
    )
    assert page is not None
    assert [event.type.value for event in page.items][-1] == (
        "execution.succeeded"
    )


def test_reported_design_preparation_conflicts_before_runtime_claim(
    tmp_path: Path,
) -> None:
    """Document the admitted/reconcile precursor at the Runtime boundary."""

    db_path = str(tmp_path / "reported-design-conflict.db")
    execution_id = "turn-fd0d579b-c9f4-4fe7-a22c-a9e9d8f48884"
    query = (
        "Please help me design the protein structure based on evolution "
        "information for gene Os01g0177400."
    )
    prepared_arguments = {
        "user_query": query,
        "obs_file_list": [],
        "resolve_gene_id": True,
        "interop_mode": "off",
        "interop_targets": [],
    }
    durable_arguments = {
        **prepared_arguments,
        "__query": query,
        "__allowed_tools": ["DigitalDesignAgent"],
        "__forced_tool": "DigitalDesignAgent",
        "__agent_slug": "design",
        "__attachment_owner": "alice",
        "__conversation": {
            "schema_version": 1,
            "conversation_key": "018fdf9e-1f0b-7a63-a5a3-5e4625b43ad6",
            "turn_id": "60",
            "mode": "expert",
            "requested_agent_id": "DigitalDesignAgent",
            "allowed_agent_ids": ["DigitalDesignAgent"],
        },
    }
    fingerprint = "d" * 64
    repository, _record = reserve_test_execution(
        db_path,
        execution_id,
        fingerprint,
        ExecutionCommand(agent_slug="design", arguments=durable_arguments),
        persist_command=True,
    )

    async def business_call() -> dict[str, str]:
        raise AssertionError(
            "business work must not start on identity conflict"
        )

    with pytest.raises(ExecutionReservationConflictError):
        asyncio.run(
            invoke_public_agent(
                db_path=db_path,
                owner="alice",
                execution_id=execution_id,
                agent_slug="design",
                arguments=prepared_arguments,
                transport="service_dispatcher",
                call=business_call,
                fingerprint_version=2,
                fingerprint=fingerprint,
            )
        )

    assert (
        repository.get(owner="alice", execution_id=execution_id).status.value
        == "admitted"
    )
    with sqlite_transaction(db_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM execution_events_v2 "
                "WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM execution_spans WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM execution_work_units "
                "WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()[0]
            == 0
        )


def test_internal_canonical_identity_starts_prepared_design_arguments(
    tmp_path: Path,
) -> None:
    """Prepared business arguments reuse the outer durable reservation."""

    db_path = str(tmp_path / "prepared-design-start.db")
    execution_id = "turn-prepared-design"
    durable_arguments = {
        "user_query": "Os01g0177400",
        "obs_file_list": [],
        "__conversation": {"mode": "expert", "turn_id": "60"},
        "__agent_slug": "design",
    }
    prepared_arguments = {
        "user_query": "Os01g0177400",
        "obs_file_list": [],
    }
    command = ExecutionCommand(
        agent_slug="design", arguments=durable_arguments
    )
    fingerprint = "e" * 64
    repository, original = reserve_test_execution(
        db_path,
        execution_id,
        fingerprint,
        command,
    )
    calls = 0

    async def business_call() -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"status": "succeeded"}

    identity = canonical_test_identity(execution_id, fingerprint, command)
    with bind_canonical_reservation_identity(identity):
        value = asyncio.run(
            invoke_public_agent(
                db_path=db_path,
                owner="alice",
                execution_id=execution_id,
                agent_slug="design",
                arguments=prepared_arguments,
                transport="service_dispatcher",
                call=business_call,
                fingerprint_version=2,
                fingerprint=fingerprint,
            )
        )

    current = repository.get(owner="alice", execution_id=execution_id)
    assert value == {"status": "succeeded"}
    assert calls == 1
    assert current.status.value == "succeeded"
    assert current.command_hash == original.command_hash


@pytest.mark.parametrize(
    "mismatch",
    [
        "owner",
        "execution_id",
        "agent",
        "fingerprint_version",
        "fingerprint",
        "command",
    ],
)
def test_canonical_identity_rejects_true_mismatch_before_business_start(
    tmp_path: Path,
    mismatch: str,
) -> None:
    """The internal carrier cannot weaken any durable replay fence."""

    db_path = str(tmp_path / f"identity-mismatch-{mismatch}.db")
    repository = SQLiteExecutionReservationRepository(db_path)
    original_command = ExecutionCommand(
        agent_slug="design", arguments={"user_query": "Os01g0177400"}
    )
    original = repository.reserve(
        owner="alice",
        execution_id="turn-identity-mismatch",
        fingerprint_version=2,
        fingerprint="a" * 64,
        command=original_command,
    )
    identity = CanonicalReservationIdentity(
        owner="alice",
        execution_id="turn-identity-mismatch",
        fingerprint_version=2,
        fingerprint="a" * 64,
        command=original_command,
    )
    if mismatch == "owner":
        identity = replace(identity, owner="mallory")
    elif mismatch == "execution_id":
        identity = replace(identity, execution_id="turn-other")
    elif mismatch == "agent":
        identity = replace(
            identity,
            command=ExecutionCommand(
                agent_slug="chat",
                arguments={"user_query": "Os01g0177400"},
            ),
        )
    elif mismatch == "fingerprint_version":
        identity = replace(identity, fingerprint_version=3)
    elif mismatch == "fingerprint":
        identity = replace(identity, fingerprint="b" * 64)
    else:
        identity = replace(
            identity,
            command=ExecutionCommand(
                agent_slug="design", arguments={"user_query": "different"}
            ),
        )
    calls = 0

    async def business_call() -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"status": "succeeded"}

    expected_error = (
        ExecutionReservationConflictError
        if mismatch == "command"
        else ExecutionRuntimeError
    )
    with (
        bind_canonical_reservation_identity(identity),
        pytest.raises(expected_error),
    ):
        asyncio.run(
            invoke_public_agent(
                db_path=db_path,
                owner="alice",
                execution_id="turn-identity-mismatch",
                agent_slug="design",
                arguments={"user_query": "prepared"},
                transport="service_dispatcher",
                call=business_call,
                fingerprint_version=2,
                fingerprint="a" * 64,
            )
        )

    current = repository.get(
        owner="alice", execution_id="turn-identity-mismatch"
    )
    assert calls == 0
    assert current.status.value == "admitted"
    assert current.command_hash == original.command_hash


def test_background_accepted_value_remains_running(tmp_path: Path) -> None:
    """Verify background accepted value remains running."""

    async def accepted():
        return ({"status": "running", "id": "legacy-run"}, 202)

    db_path = tmp_path / "accepted.db"
    value = asyncio.run(
        invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id="turn-accepted",
            agent_slug="analyst",
            arguments={"query": "rice"},
            transport="background",
            call=accepted,
        )
    )
    assert value == ({"status": "running", "id": "legacy-run"}, 202)
    assert (
        SQLiteExecutionReservationRepository(str(db_path))
        .get(owner="alice", execution_id="turn-accepted")
        .status.value
        == "running"
    )


def test_runtime_can_adopt_one_preallocated_domain_run_id(
    tmp_path: Path,
) -> None:
    """Verify runtime can adopt one preallocated domain run ID."""

    async def business_call() -> dict[str, str]:
        return {"status": "ok"}

    db_path = tmp_path / "preallocated-run.db"
    result = asyncio.run(
        invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id="turn-preallocated",
            agent_slug="research",
            arguments={"query": "rice"},
            transport="authenticated_http",
            call=business_call,
            run_id="run-research-preallocated",
        )
    )

    assert result == {"status": "ok"}
    record = SQLiteExecutionReservationRepository(str(db_path)).get(
        owner="alice", execution_id="turn-preallocated"
    )
    assert record.run_id == "run-research-preallocated"


def test_equivalent_transports_share_catalog_driver_and_semantic_facts(
    tmp_path: Path,
) -> None:
    """Presentation adapters may differ, but runtime semantics may not."""

    transports = ("authenticated_http", "expert_router", "mcp")
    semantic_facts: dict[str, list[tuple[Any, ...]]] = {}
    resolved_drivers: dict[str, str] = {}

    async def business_call() -> dict[str, str]:
        return {"answer": "transport-neutral"}

    for transport in transports:
        db_path = tmp_path / f"semantic-{transport}.db"
        result = asyncio.run(
            invoke_public_agent(
                db_path=str(db_path),
                owner="alice",
                execution_id=f"turn-semantic-{transport}",
                agent_slug="chat",
                arguments={
                    "query": "rice",
                    "__assistant_message_id": "msg-semantic",
                },
                transport=transport,
                call=business_call,
            )
        )
        assert result == {"answer": "transport-neutral"}
        spec = public_agent_spec("chat")
        assert spec is not None
        resolved_drivers[transport] = spec.driver
        page = SQLiteExecutionJournal(str(db_path)).list_events(
            f"turn-semantic-{transport}", owner="alice", limit=20
        )
        assert page is not None
        semantic_facts[transport] = [
            (
                event.type.value,
                event.status.value,
                event.source,
                event.summary.key,
                event.public_payload,
            )
            for event in page.items
        ]

    assert set(resolved_drivers.values()) == {"resumable_graph"}
    assert (
        semantic_facts["authenticated_http"] == semantic_facts["expert_router"]
    )
    assert semantic_facts["authenticated_http"] == semantic_facts["mcp"]


def test_native_nested_result_is_published_without_transport_business_copy(
    tmp_path: Path,
) -> None:
    """The adapter projects the existing formatted/execution envelope."""

    async def business_call() -> tuple[dict[str, object], int]:
        return (
            {
                "result": {
                    "formatted": {
                        "answer": "durable formatted answer",
                        "follow_up_questions": ["What next?"],
                    },
                    "execution": {
                        "artifacts": [
                            {
                                "id": "artifact-safe-1",
                                "role": "report",
                                "name": "report.md",
                                "media_type": "text/markdown",
                                "size_bytes": 42,
                            },
                            {
                                "id": "C:/private/report.md",
                                "role": "unsafe",
                                "name": "C:/private/report.md",
                            },
                        ]
                    },
                }
            },
            200,
        )

    db_path = tmp_path / "nested-result.db"
    value = asyncio.run(
        invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id="turn-nested-result",
            agent_slug="chat",
            arguments={"query": "rice"},
            transport="authenticated_http",
            call=business_call,
        )
    )
    assert value[1] == 200
    page = SQLiteExecutionJournal(str(db_path)).list_events(
        "turn-nested-result", owner="alice", limit=30
    )
    assert page is not None
    message_events = [
        event
        for event in page.items
        if event.type.value == "message.completed"
    ]
    assert len(message_events) == 1
    assert message_events[0].public_payload.model_dump()["text"] == (
        "durable formatted answer"
    )
    artifact_events = [
        event
        for event in page.items
        if event.type.value == "artifact.published"
    ]
    assert len(artifact_events) == 1
    assert artifact_events[0].target is not None
    assert artifact_events[0].target.id == "artifact-safe-1"


def test_cited_result_publishes_safe_references_with_the_completed_message(
    tmp_path: Path,
) -> None:
    """V2 keeps cited answers reconstructible without exposing direct URLs."""

    async def business_call() -> dict[str, object]:
        return {
            "result": {
                "formatted": {
                    "answer": "Evidence-backed answer.<sup>1</sup>",
                    "references": [
                        {
                            "file_id": "private-source-identity",
                            "title": "Drought epigenetics",
                            "au": "Smith J",
                            "ti": "Drought epigenetics",
                            "so": "Plant Journal",
                            "py": "2026",
                            "di": "10.1000/safe-doi",
                            "dl": "https://doi.org/10.1000/safe-doi",
                            "formatted_citation": (
                                "Smith J. [https://doi.org/10.1000/safe-doi]"
                            ),
                        }
                    ],
                }
            }
        }

    db_path = tmp_path / "cited-result.db"
    asyncio.run(
        invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id="turn-cited-result",
            agent_slug="review",
            arguments={"query": "rice"},
            transport="authenticated_http",
            call=business_call,
        )
    )

    page = SQLiteExecutionJournal(str(db_path)).list_events(
        "turn-cited-result", owner="alice", limit=30
    )
    assert page is not None
    completed = first_event_of_type(
        page.items,
        ExecutionEventType.MESSAGE_COMPLETED,
    )
    assert completed.to_public_dict()["public_payload"]["references"] == [
        {
            "title": "Drought epigenetics",
            "au": "Smith J",
            "ti": "Drought epigenetics",
            "so": "Plant Journal",
            "py": "2026",
            "di": "10.1000/safe-doi",
        }
    ]
